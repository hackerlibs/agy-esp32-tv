#pragma once

/*
 * USB Serial/JTAG link to the host bridge.
 *
 * Line protocol, ASCII, one frame per line (backward-compatible ZTV1):
 *
 *   host -> device   ZTV1 S st=<idle|think|tool|perm|err|done> tool="<tok>" n=<int>
 *                    f=<int> el=<sec> todo=<done>/<total> cur="<str>" msg="<str>"
 *                    proj="<tok>" sid="<tok>" clk="HH:MM:SS" date="<str>"
 *                    [model="<str>" inv=<int> step=<int> ctx=<int> out=<int> sub=<int> bg=<int>]
 *                    (every frame is a complete snapshot; unknown keys are ignored)
 *
 *   host -> device   ZTV1 C ping | demo=<0|1> | rot=<0..3> | stats | get | bl | reboot
 *   device -> host   ZTV1 R <text>
 */
void zlink_start(void);
