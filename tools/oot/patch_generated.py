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

EDITS = [
    ('main_0087.c', 'void f_70006e6be0(Cpu *c) {\n', DYN_DT + 'void f_70006e6be0(Cpu *c) {\n'),
    ('main_0087.c', '    fp_op_s(c, FOP_DIV, 8, 1, 0);\n', '    fp_op_s(c, FOP_DIV, 8, 1, 0); oot_dyn_dt(c);   /* real time step, main+0x6e6dc4 */\n'),
    ('main_0080.c', 'void f_700062ac50(Cpu *c) {\n    FN_ENTER(0x700062ac50ull);\n',
     'extern void ov_camera_mouse(uint64_t node);\nvoid f_700062ac50(Cpu *c) {\n    FN_ENTER(0x700062ac50ull);\n'
     '    ov_camera_mouse(c->x[0]);   /* the mouse turns the camera: main+0x62ac50 takes the camera node in x0 */\n'),
    ('main_0133.c', 'L_7000c93940:;\n', 'L_7000c93940:;\n    if (x19 == 0) { goto L_7000c93a18; }   /* an unloaded resource takes the not-found path; was a read of 0x150 */\n'),
]


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
        print('ok')
        return
    root = Path(sys.argv[1]) / 'main'
    for name, old, new in EDITS:
        p = root / name
        text = p.read_bytes().decode('utf-8', 'surrogateescape')
        p.write_bytes(apply(text, old, new).encode('utf-8', 'surrogateescape'))
    print('patch_generated: ok')


if __name__ == '__main__':
    main()
