# Remote pulse-control audit — November 19, 2026

The winch is controlled by relay contacts across its remote buttons. Pulse duration is variable; motor speed is not. The roof Pi uses encoder feedback to choose successive button-press durations. Automatic inspection and manual distance moves use this same controller. Short manual jogs use one fixed direction pulse.

## Controls and current saved values

These values were read from the roof Pi during this audit, not inferred from source defaults. They are preserved by the update.

| Control | Up | Down | Effect |
|---|---:|---:|---|
| First / short-jog pulse | 310 ms | 310 ms | First press of each measured jog; also the whole direction press for a short manual jog. |
| Minimum correction | 210 ms | 210 ms | Lower bound on subsequent adaptive presses. |
| Maximum correction | 520 ms | 530 ms | Upper bound on subsequent adaptive presses. |
| Wait after each pulse | 1000 ms | 1000 ms | Minimum wait after the direction press and any Stop press finish, before judging the result. |
| Adaptive adjustment | On | On | Later presses use observed travel and remaining distance. Off repeats the first-pulse duration. |

Start and Stop presses are fixed at 300 ms. Start has a further 200 ms gap before direction. Thus a 310 ms direction press with the current one-second wait takes about 2.11 seconds per complete Start–direction–Stop–wait cycle, plus scheduling overhead. The adjustable pulse is only one part of that cycle.

The UI keeps minimum ≤ first pulse ≤ maximum, so changing a limit across the first-pulse value also changes that first pulse. The labels now explain this interaction.

## How adaptation works

Each measured jog starts with the chosen first pulse. After a pulse, the controller measures settled travel divided by the actual time the direction relay was closed. Later requested duration is approximately:

`remaining inches × direction fraction ÷ observed inches per millisecond`

The direction fractions remain 0.90 Up and 0.45 Down. Down deliberately approaches more conservatively. Requests are limited to the configured minimum/maximum and can grow only after meaningful progress (at most 1.75× Up or 1.15× Down). A faster response updates the estimate immediately; slower responses reduce it gradually. A pulse can release early on entering the half-inch window.

The response estimate resets for each measured jog, including a new internal jog within a long move. This is adaptation within a move, not permanent learning. If min/max constraints dominate, many requested pulses can legitimately be identical. The effective response includes coasting and remote delay; it is not a direct motor-speed measurement or a calibrated latency model.

## Findings and changes

1. **No-response retry could shrink below a useful button press.** The installed controller's last Down move reported two pulses, ending at 210 ms, with no response estimate. The old fallback changed the initial 310 ms press to a shorter distance-based press after no movement. The update repeats the preceding duration once; it never increases an unconfirmed pulse. Two pulses without meaningful progress still stop and disarm.
2. **Fixed waiting did not prove the mechanism had settled.** A correction could be sent while late motion continued. The update requires the configured wait plus a quiet encoder interval of 150 ms. If significant motion continues beyond the wait plus two seconds, it stops and disarms instead of adding a correction.
3. **Tiny encoder changes could count as successful travel.** The old threshold was three counts (about 0.0046 inches with the current pulley calibration). Meaningful progress now requires at least 0.02 inches and at least three counts. Smaller changes do not enable pulse growth or reset the no-progress guard.
4. **The interface hid actual pulse decisions.** It now shows requested duration, correction/retry reason, phase, and the last settled pulse's measured travel and actual relay-on duration. The first-pulse and correction controls are explained separately.

The half-inch acceptance window, strict route lower limit, quarter-turn reverse guard, feedback freshness requirement, browser lease, overall timeout, pulse limits and explicit Stop behavior are retained. Up/Down fractions and saved timing values were not increased. No PID or continuous speed control was added to the on/off remote.

## Verification and limits

Thirty-one Python checks pass, including the original controller tests, X zero and new virtual-clock tests using the real relay driver with delayed actuation/coasting. They cover variable pulse lengths, half-inch completion, one ignored press recovering, noise-only failure, no correction during late coasting, continued-motion rejection and expired-lease stopping. Twelve JavaScript route checks also pass, including preserving brick identities and duplicate-tap protection after X zero and stopping if the X reference changes during a route.

In one delayed-winch simulation using a 310/210/530 ms profile and a six-inch target, Up requested 310, 530, 530, 530, 298 ms and settled at 5.67 inches; Down requested 310, 356, 409, 470, 330, 210, 210 ms and settled at 5.69 inches. These are simulation results, not measured hardware performance. Remote radio dead time, minimum useful press and physical stopping distance still need observation on the rig; the model does not establish those values automatically.

## Automatic scoring and map colors

Automatic stops already save the three-tap recording and its spectral analysis, then compare it with the saved baseline. Difference below 10 is green; above 10 is red. Exactly 10, no baseline or unavailable analysis remains unclassified. This is a difference score, not a percent similarity. Stored features permit recalculation when the baseline changes; backup includes those features and the baseline.

The manual **Different** review button previously selected the yellow review state. It now selects red. Older manual Different records also render red without rewriting stored evidence. A real Chrome session against the isolated simulator confirmed automatic analysis/storage/map placement, baseline-required behavior, green matching results, red differing results, numeric scores, backup inclusion and new/legacy manual Different colors.

## X zero

The accompanying requested **Set X zero here** control appears beside X position in Manual. It changes only a coordinate offset; it sends no motor command and does not alter the Nano firmware or raw count. Right remains positive, left negative, and Y zero is unchanged. Set X zero again after the roof controller restarts or the Nano reconnects. Matching wall references shift by the same coordinate offset so saved brick identities/reservations remain usable; original recorded coordinates are retained. A separate local Chrome test verified zero at a simulated three-inch position, no movement command when zeroing, unchanged Y, and a subsequent one-inch Left move reading negative one inch.

## Installation

Installed on the roof Pi at 09:29 UTC, November 19. Backup: `/var/backups/brick-mapper/20261119T092931Z-pulse-audit/`. The controller is active, tested source/served files match, both encoders are connected and the timing values above are unchanged. The Nano reset its raw reference during the service restart, so **Y zero must be set again at the roof reference**. No physical zero was guessed and no physical motion/tapping test was run. Refresh the page for the new controls.
