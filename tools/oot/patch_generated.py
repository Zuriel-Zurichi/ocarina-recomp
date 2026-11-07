"""Applies Ocarina's source edits to the generated C of main. Run after recomp/gen.py.

Lifted code holds no room for these through recomp/patches.py (one needs a branch, the other host code), and gen.py
regenerates its output, so they live here. Each edit is checked: a generated file that does not match stops the build.

    python tools/oot/patch_generated.py <generated folder>

Run it twice and nothing changes. `python tools/oot/patch_generated.py --test` checks the logic without game files.
"""
import sys
from pathlib import Path

DYN_DT = r'''/* The frame's time step from the measured frame time, smoothed over 8 frames and capped at 1/10 s, so the game keeps real
   speed below its nominal rate (the logic of the "30fpsDynamic no SLOWDOWN" mod). OOT_DYN_DT=0 keeps the fixed step. */
extern uint64_t plat_now_ns(void);
extern char *getenv(const char *);
static void oot_dyn_dt(Cpu *c) {
    static int on = -1;
    static uint64_t last;
    static int64_t smooth;
    if (on < 0) { const char *e = getenv("OOT_DYN_DT"); on = !(e && *e == '0'); }
    if (!on) return;
    uint64_t now = plat_now_ns();
    uint64_t prev = last;
    last = now;
    if (!prev) return;
    int64_t d = (int64_t)(now - prev);
    if (d > 100000000) d = 100000000;
    if (smooth && d > smooth * 3) d = smooth;   /* a hitch stays out of the average: the frames after it would run fast */
    if (!smooth) smooth = d;
    smooth += (d - smooth) >> 3;
    FP_SETS(8, (float)((double)smooth / 1e9) * VSF32(1));
}

'''

FIXED_RES = r'''/* Fixed 3D resolution (OOT_RESOLUTION in the ini, TOTK_OOT_RESOLUTION in the environment, live): the scene at a set fraction of the output instead of the game's dynamic
   resolution (~1706x960 for 1920x1080). The flag at +0x94 (main+0x805bd0, the site of Lichii's "Max Resolution" patch)
   picks the GPU-time controller; the other path (L_700080640c) returns a fixed scale, 1.0 as shipped. The dynamic path
   runs 30 frames first: it also writes fields the rest of the frame reads (+8, +16, +168), and the fixed path taken
   from the first frame left them unset and the 3D black. After NotRexed/Hookshot c02f290d47ff (nx2/oot_patches.py,
   host/nn_vi.c, GPL-3.0). "auto": the game's own; a percentage 25..200 (above 100 untested). */
extern const char *cfg_get(const char *);
extern unsigned cfg_version(void);
extern void rt_log(const char *, ...);
extern double strtod(const char *, char **);
static int oot_dynres_runs;
static uint32_t oot_res_bits = 0x3f800000u;
static int oot_fixed_res(void) {
    static unsigned seen = ~0u;
    static int fixed;
    unsigned v = cfg_version();
    if (v != seen) {
        seen = v;
        // cfg_get, not cfg_value: a runtime built before the setting existed has no table row, and cfg_value made that fatal
        const char *s = cfg_get("TOTK_OOT_RESOLUTION");
        if (!s || !*s) s = "auto";
        char *end;
        double pct = strtod(s, &end);
        fixed = end != s && !*end && pct >= 25 && pct <= 200;
        if (fixed) { union { float f; uint32_t u; } k = {(float)(pct / 100)}; oot_res_bits = k.u; }
        rt_log("resolution: 3D %s%s", fixed ? s : "the game's dynamic resolution", fixed ? " % of the output" : "");
    }
    return fixed && oot_dynres_runs >= 30;
}

'''

EDITS = [
    ('main_0087.c', 'void f_70006e6be0(Cpu *c) {\n', DYN_DT + 'void f_70006e6be0(Cpu *c) {\n'),
    ('main_0087.c', '    fp_op_s(c, FOP_DIV, 8, 1, 0);\n', '    fp_op_s(c, FOP_DIV, 8, 1, 0); oot_dyn_dt(c);   /* real time step, main+0x6e6dc4 */\n'),
    ('main_0080.c', 'void f_700062ac50(Cpu *c) {\n    FN_ENTER(0x700062ac50ull);\n',
     'extern void ov_camera_mouse(uint64_t node);\nvoid f_700062ac50(Cpu *c) {\n    FN_ENTER(0x700062ac50ull);\n'
     '    ov_camera_mouse(c->x[0]);   /* the mouse turns the camera: main+0x62ac50 takes the camera node in x0 */\n'),
    ('main_0347.c', 'void f_70027a0f90(Cpu *c) {\n    FN_ENTER(0x70027a0f90ull);\n',
     'extern void ov_camera_aim_mouse(uint64_t node, uint64_t angles);\n'
     'void f_70027a0f90(Cpu *c) {\n    FN_ENTER(0x70027a0f90ull);\n'
     '    ov_camera_aim_mouse(c->x[0], c->x[1]);   /* item aiming: node and distance/pitch/yaw before the camera transform */\n'),
    ('main_0348.c', 'void f_70027c1880(Cpu *c) {\n',
     'extern int ov_camera_tail_mouse_idle(uint64_t node, int idle);\nvoid f_70027c1880(Cpu *c) {\n'),
    ('main_0348.c', '    if (((x0 >> 0) & 1) == 0) { goto L_70027c18d4; }\n',
     '    x0 = ov_camera_tail_mouse_idle(x19, (int)(x0 & 1));   /* raw mouse also activates Tail manual rotation */\n'
     '    if (((x0 >> 0) & 1) == 0) { goto L_70027c18d4; }\n'),
    ('main_0098.c', 'void f_7000805aa0(Cpu *c) {\n', FIXED_RES + 'void f_7000805aa0(Cpu *c) {\n'),
    ('main_0098.c', '    if (((x8 >> 0) & 1) == 0) { goto L_700080640c; }\n',
     '    if (oot_fixed_res() || ((x8 >> 0) & 1) == 0) { goto L_700080640c; }\n    oot_dynres_runs++;\n'),
    ('main_0098.c', '    x9 = (uint32_t)(0x3f800000ull);\n    goto L_7000806000;\n',
     '    x9 = oot_fixed_res() ? oot_res_bits : 0x3f800000u;   /* the fixed path\'s scale */\n    goto L_7000806000;\n'),
    ('main_0133.c', 'L_7000c93940:;\n', 'L_7000c93940:;\n    if (x19 == 0) { goto L_7000c93a18; }   /* an unloaded resource takes the not-found path; was a read of 0x150 */\n'),
]

# Shared angular-input helpers: their return is an angle delta, not stick input.
# Scoped to the function so the common generated return sequence stays unique.
AXIS_HELPERS = [('main_0078.c', '7000623ac0', 1), ('main_0078.c', '7000623e70', 0)]


def apply_axis_helper(text, address, axis):
    old = f'void f_{address}(Cpu *c) {{\n    FN_ENTER(0x{address}ull);\n'
    new = ('extern float ov_camera_axis_mouse(uint64_t node, int axis, float delta, float speed);\n' + old +
           '    uint64_t oot_mouse_node = c->x[0];\n    float oot_mouse_speed = VSF32(0);\n')
    text = apply(text, old, new)
    start = text.index(new)
    end = text.index('\n}\n', start) + 3
    body = text[start:end]
    ret = 'c->x[29] = x29; c->x[30] = x30; c->sp = sp_; return;'
    hooked = ('c->x[29] = x29; c->x[30] = x30; c->sp = sp_; '
              f'FP_SETS(0, ov_camera_axis_mouse(oot_mouse_node, {axis}, VSF32(0), oot_mouse_speed)); return;')
    return text[:start] + apply(body, ret, hooked) + text[end:]


def apply(text, old, new):
    if new in text:
        return text
    if text.count(old) != 1:
        raise SystemExit(f'patch_generated: {old.strip()!r} found {text.count(old)} times: this is not the code of game version 1.1.1')
    return text.replace(old, new)


def main():
    if sys.argv[1:] == ['--test']:
        once = apply('a\nb\n', 'a\n', 'a\nx\n')
        assert once == 'a\nx\nb\n' and apply(once, 'a\n', 'a\nx\n') == once
        for _, old, new in EDITS:
            patched = apply(old, old, new)
            assert patched == new and apply(patched, old, new) == patched
        for _, address, axis in AXIS_HELPERS:
            fixture = (f'void f_{address}(Cpu *c) {{\n    FN_ENTER(0x{address}ull);\n'
                       '    c->x[29] = x29; c->x[30] = x30; c->sp = sp_; return;\n}\n')
            patched = apply_axis_helper(fixture, address, axis)
            assert 'ov_camera_axis_mouse(oot_mouse_node' in patched
            assert apply_axis_helper(patched, address, axis) == patched
        print('ok')
        return
    root = Path(sys.argv[1]) / 'main'
    pending = {}
    for name, old, new in EDITS:
        p = root / name
        text = pending.get(name)
        if text is None:
            text = p.read_bytes().decode('utf-8', 'surrogateescape')
        pending[name] = apply(text, old, new)
    for name, address, axis in AXIS_HELPERS:
        text = pending.get(name)
        if text is None:
            text = (root / name).read_bytes().decode('utf-8', 'surrogateescape')
        pending[name] = apply_axis_helper(text, address, axis)
    # Check every anchor before writing anything; unchanged files keep their timestamps.
    for name, text in pending.items():
        p = root / name
        data = text.encode('utf-8', 'surrogateescape')
        if p.read_bytes() != data:
            p.write_bytes(data)
    print('patch_generated: ok')


if __name__ == '__main__':
    main()
