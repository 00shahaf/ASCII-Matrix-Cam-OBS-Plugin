/*
 * ASCII Cam: an OBS video filter that redraws a source as ASCII art, blocks or
 * Matrix-style rain. All the work happens on the GPU in data/ascii-cam.effect.
 */
#include <obs-module.h>
#include <graphics/vec2.h>
#include <graphics/vec4.h>
#include <util/bmem.h>
#include <util/platform.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

OBS_DECLARE_MODULE()
OBS_MODULE_AUTHOR("ASCII Cam")

#define LOG(level, fmt, ...) blog(level, "[ascii-cam] " fmt, ##__VA_ARGS__)

struct ascii_filter {
	obs_source_t *context;
	gs_effect_t *effect;
	gs_texture_t *atlas;

	gs_eparam_t *p_atlas, *p_grid, *p_mode, *p_edges, *p_edge_threshold, *p_source_colour, *p_tint, *p_bg,
		*p_brightness, *p_contrast, *p_gamma, *p_invert, *p_elapsed;

	int mode;
	int cell_height;
	bool edges;
	float edge_threshold;
	bool source_colour;
	struct vec4 tint, bg;
	float brightness, contrast, gamma;
	bool invert;
	float elapsed;
};

/* Loads the effect and atlas. Call inside obs_enter_graphics(). */
static bool load_resources(gs_effect_t **effect, gs_texture_t **atlas)
{
	char *effect_path = obs_module_file("ascii-cam.effect");
	char *atlas_path = obs_module_file("atlas.png");
	char *errors = NULL;

	*effect = effect_path ? gs_effect_create_from_file(effect_path, &errors) : NULL;
	if (!*effect)
		LOG(LOG_ERROR, "couldn't compile %s: %s", effect_path ? effect_path : "ascii-cam.effect",
		    errors ? errors : "(file not found)");
	*atlas = atlas_path ? gs_texture_create_from_file(atlas_path) : NULL;
	if (!*atlas)
		LOG(LOG_ERROR, "couldn't load %s", atlas_path ? atlas_path : "atlas.png");

	bfree(errors);
	bfree(effect_path);
	bfree(atlas_path);
	return *effect && *atlas;
}

static const char *ascii_get_name(void *unused)
{
	UNUSED_PARAMETER(unused);
	return "ASCII Cam";
}

static void ascii_update(void *data, obs_data_t *s)
{
	struct ascii_filter *f = data;
	f->mode = (int)obs_data_get_int(s, "mode");
	f->cell_height = (int)obs_data_get_int(s, "cell_height");
	f->edges = obs_data_get_bool(s, "edges");
	f->edge_threshold = (float)obs_data_get_double(s, "edge_threshold");
	f->source_colour = obs_data_get_int(s, "colour_mode") == 0;
	vec4_from_rgba(&f->tint, (uint32_t)obs_data_get_int(s, "tint"));
	vec4_from_rgba(&f->bg, (uint32_t)obs_data_get_int(s, "background"));
	f->brightness = (float)obs_data_get_double(s, "brightness");
	f->contrast = (float)obs_data_get_double(s, "contrast");
	f->gamma = (float)obs_data_get_double(s, "gamma");
	f->invert = obs_data_get_bool(s, "invert");
}

static void ascii_defaults(obs_data_t *s)
{
	obs_data_set_default_int(s, "mode", 0);
	obs_data_set_default_int(s, "cell_height", 14);
	obs_data_set_default_bool(s, "edges", true);
	obs_data_set_default_double(s, "edge_threshold", 0.25);
	obs_data_set_default_int(s, "colour_mode", 0);
	obs_data_set_default_int(s, "tint", 0xFF66FF40);       /* 0xAABBGGRR: Matrix green */
	obs_data_set_default_int(s, "background", 0xFF000000); /* opaque black */
	obs_data_set_default_double(s, "brightness", 0.0);
	obs_data_set_default_double(s, "contrast", 1.3);
	obs_data_set_default_double(s, "gamma", 0.85);
	obs_data_set_default_bool(s, "invert", false);
}

static void *ascii_create(obs_data_t *settings, obs_source_t *source)
{
	struct ascii_filter *f = bzalloc(sizeof(*f));
	f->context = source;

	obs_enter_graphics();
	if (load_resources(&f->effect, &f->atlas)) {
		gs_effect_t *e = f->effect;
		f->p_atlas = gs_effect_get_param_by_name(e, "atlas");
		f->p_grid = gs_effect_get_param_by_name(e, "grid");
		f->p_mode = gs_effect_get_param_by_name(e, "mode");
		f->p_edges = gs_effect_get_param_by_name(e, "edges");
		f->p_edge_threshold = gs_effect_get_param_by_name(e, "edge_threshold");
		f->p_source_colour = gs_effect_get_param_by_name(e, "source_colour");
		f->p_tint = gs_effect_get_param_by_name(e, "tint");
		f->p_bg = gs_effect_get_param_by_name(e, "bg");
		f->p_brightness = gs_effect_get_param_by_name(e, "brightness");
		f->p_contrast = gs_effect_get_param_by_name(e, "contrast");
		f->p_gamma = gs_effect_get_param_by_name(e, "gamma");
		f->p_invert = gs_effect_get_param_by_name(e, "invert");
		f->p_elapsed = gs_effect_get_param_by_name(e, "elapsed");
	}
	obs_leave_graphics();

	ascii_update(f, settings);
	return f;
}

static void ascii_destroy(void *data)
{
	struct ascii_filter *f = data;
	obs_enter_graphics();
	gs_effect_destroy(f->effect);
	gs_texture_destroy(f->atlas);
	obs_leave_graphics();
	bfree(f);
}

static void ascii_tick(void *data, float seconds)
{
	struct ascii_filter *f = data;
	f->elapsed += seconds;
	if (f->elapsed > 10000.0f) /* keep float precision; the rain jumps once every ~3 hours */
		f->elapsed -= 10000.0f;
}

static void set_params(struct ascii_filter *f, uint32_t width, uint32_t height)
{
	float ch = (float)(f->cell_height > 2 ? f->cell_height : 2);
	float cw = ch * 0.5f; /* monospace cells are about twice as tall as wide */
	struct vec2 grid;
	vec2_set(&grid, (float)width / cw, (float)height / ch);

	gs_effect_set_texture(f->p_atlas, f->atlas);
	gs_effect_set_vec2(f->p_grid, &grid);
	gs_effect_set_int(f->p_mode, f->mode);
	gs_effect_set_bool(f->p_edges, f->edges);
	gs_effect_set_float(f->p_edge_threshold, f->edge_threshold);
	gs_effect_set_bool(f->p_source_colour, f->source_colour);
	gs_effect_set_vec4(f->p_tint, &f->tint);
	gs_effect_set_vec4(f->p_bg, &f->bg);
	gs_effect_set_float(f->p_brightness, f->brightness);
	gs_effect_set_float(f->p_contrast, f->contrast);
	gs_effect_set_float(f->p_gamma, f->gamma);
	gs_effect_set_bool(f->p_invert, f->invert);
	gs_effect_set_float(f->p_elapsed, f->elapsed);
}

static void ascii_render(void *data, gs_effect_t *unused)
{
	UNUSED_PARAMETER(unused);
	struct ascii_filter *f = data;
	obs_source_t *target = obs_filter_get_target(f->context);
	uint32_t width = obs_source_get_base_width(target);
	uint32_t height = obs_source_get_base_height(target);

	if (!f->effect || !f->atlas || !width || !height) {
		obs_source_skip_video_filter(f->context);
		return;
	}
	if (!obs_source_process_filter_begin(f->context, GS_RGBA, OBS_ALLOW_DIRECT_RENDERING))
		return;
	set_params(f, width, height);
	gs_blend_state_push();
	gs_blend_function(GS_BLEND_ONE, GS_BLEND_INVSRCALPHA);
	obs_source_process_filter_end(f->context, f->effect, width, height);
	gs_blend_state_pop();
}

static bool colour_mode_changed(obs_properties_t *props, obs_property_t *p, obs_data_t *s)
{
	UNUSED_PARAMETER(p);
	obs_property_set_visible(obs_properties_get(props, "tint"), obs_data_get_int(s, "colour_mode") == 1);
	return true;
}

static bool mode_changed(obs_properties_t *props, obs_property_t *p, obs_data_t *s)
{
	UNUSED_PARAMETER(p);
	bool ascii = obs_data_get_int(s, "mode") == 0;
	obs_property_set_visible(obs_properties_get(props, "edges"), ascii);
	obs_property_set_visible(obs_properties_get(props, "edge_threshold"), ascii);
	return true;
}

static obs_properties_t *ascii_properties(void *data)
{
	UNUSED_PARAMETER(data);
	obs_properties_t *props = obs_properties_create();

	obs_property_t *mode =
		obs_properties_add_list(props, "mode", "Style", OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(mode, "ASCII", 0);
	obs_property_list_add_int(mode, "Blocks", 1);
	obs_property_list_add_int(mode, "Matrix rain", 2);
	obs_property_set_modified_callback(mode, mode_changed);

	obs_properties_add_int_slider(props, "cell_height", "Character size (px)", 4, 64, 1);
	obs_properties_add_bool(props, "edges", "Draw edges with | / - \\");
	obs_properties_add_float_slider(props, "edge_threshold", "Edge sensitivity (lower = more edges)", 0.05,
					1.0, 0.01);

	obs_property_t *cm = obs_properties_add_list(props, "colour_mode", "Colour", OBS_COMBO_TYPE_LIST,
						     OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(cm, "Source colour", 0);
	obs_property_list_add_int(cm, "Single colour", 1);
	obs_property_set_modified_callback(cm, colour_mode_changed);
	obs_properties_add_color(props, "tint", "Character colour");
	obs_properties_add_color_alpha(props, "background", "Background (lower alpha to see through)");

	obs_properties_add_float_slider(props, "brightness", "Brightness", -0.5, 0.5, 0.01);
	obs_properties_add_float_slider(props, "contrast", "Contrast", 0.5, 3.0, 0.01);
	obs_properties_add_float_slider(props, "gamma", "Gamma (lower = brighter mids)", 0.3, 2.5, 0.01);
	obs_properties_add_bool(props, "invert", "Invert (dense characters for dark areas)");
	return props;
}

static struct obs_source_info ascii_filter_info = {
	.id = "ascii_cam_filter",
	.type = OBS_SOURCE_TYPE_FILTER,
	.output_flags = OBS_SOURCE_VIDEO,
	.get_name = ascii_get_name,
	.create = ascii_create,
	.destroy = ascii_destroy,
	.update = ascii_update,
	.get_defaults = ascii_defaults,
	.get_properties = ascii_properties,
	.video_tick = ascii_tick,
	.video_render = ascii_render,
};

/*
 * Self-test, only when ASCII_CAM_SELFTEST=<input image>:<output prefix> is set:
 * renders the input through the effect in each style and writes <prefix>-<n>.ppm,
 * so the shader can be checked without clicking through OBS.
 */
static void write_ppm(const char *path, const uint8_t *rgba, uint32_t linesize, uint32_t w, uint32_t h)
{
	FILE *fp = fopen(path, "wb");
	if (!fp)
		return;
	fprintf(fp, "P6\n%u %u\n255\n", w, h);
	for (uint32_t y = 0; y < h; y++)
		for (uint32_t x = 0; x < w; x++)
			fwrite(rgba + y * linesize + x * 4, 1, 3, fp);
	fclose(fp);
}

static void self_test(const char *spec)
{
	char *copy = bstrdup(spec);
	char *sep = strrchr(copy, ':');
	if (!sep) {
		bfree(copy);
		return;
	}
	*sep = 0;
	const char *input = copy, *prefix = sep + 1;

	obs_enter_graphics();
	struct ascii_filter f = {0};
	gs_texture_t *src = gs_texture_create_from_file(input);
	if (src && load_resources(&f.effect, &f.atlas)) {
		uint32_t w = gs_texture_get_width(src), h = gs_texture_get_height(src);
		gs_eparam_t *p_image = gs_effect_get_param_by_name(f.effect, "image");
		gs_effect_t *e = f.effect;
		f.p_atlas = gs_effect_get_param_by_name(e, "atlas");
		f.p_grid = gs_effect_get_param_by_name(e, "grid");
		f.p_mode = gs_effect_get_param_by_name(e, "mode");
		f.p_edges = gs_effect_get_param_by_name(e, "edges");
		f.p_edge_threshold = gs_effect_get_param_by_name(e, "edge_threshold");
		f.p_source_colour = gs_effect_get_param_by_name(e, "source_colour");
		f.p_tint = gs_effect_get_param_by_name(e, "tint");
		f.p_bg = gs_effect_get_param_by_name(e, "bg");
		f.p_brightness = gs_effect_get_param_by_name(e, "brightness");
		f.p_contrast = gs_effect_get_param_by_name(e, "contrast");
		f.p_gamma = gs_effect_get_param_by_name(e, "gamma");
		f.p_invert = gs_effect_get_param_by_name(e, "invert");
		f.p_elapsed = gs_effect_get_param_by_name(e, "elapsed");
		obs_data_t *s = obs_data_create();
		ascii_defaults(s);

		gs_texrender_t *tr = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
		gs_stagesurf_t *stage = gs_stagesurface_create(w, h, GS_RGBA);
		for (int mode = 0; mode < 3; mode++) {
			obs_data_set_int(s, "mode", mode);
			obs_data_set_int(s, "colour_mode", mode == 2 ? 1 : 0);
			ascii_update(&f, s);
			f.elapsed = 3.0f;
			gs_texrender_reset(tr);
			if (gs_texrender_begin(tr, w, h)) {
				struct vec4 clear;
				vec4_zero(&clear);
				gs_clear(GS_CLEAR_COLOR, &clear, 0.0f, 0);
				gs_ortho(0.0f, (float)w, 0.0f, (float)h, -100.0f, 100.0f);
				gs_effect_set_texture(p_image, src);
				set_params(&f, w, h);
				while (gs_effect_loop(f.effect, "Draw"))
					gs_draw_sprite(src, 0, w, h);
				gs_texrender_end(tr);
			}
			gs_stage_texture(stage, gs_texrender_get_texture(tr));
			uint8_t *pixels;
			uint32_t linesize;
			if (gs_stagesurface_map(stage, &pixels, &linesize)) {
				char path[1024];
				snprintf(path, sizeof(path), "%s-%d.ppm", prefix, mode);
				write_ppm(path, pixels, linesize, w, h);
				gs_stagesurface_unmap(stage);
				LOG(LOG_INFO, "self-test wrote %s", path);
			}
		}
		gs_stagesurface_destroy(stage);
		gs_texrender_destroy(tr);
		obs_data_release(s);
	} else {
		LOG(LOG_ERROR, "self-test couldn't load %s or the plugin data", input);
	}
	gs_texture_destroy(src);
	gs_effect_destroy(f.effect);
	gs_texture_destroy(f.atlas);
	obs_leave_graphics();
	bfree(copy);
}

bool obs_module_load(void)
{
	obs_register_source(&ascii_filter_info);
	LOG(LOG_INFO, "loaded");
	return true;
}

void obs_module_post_load(void)
{
	const char *spec = getenv("ASCII_CAM_SELFTEST");
	if (spec && *spec)
		self_test(spec);
}
