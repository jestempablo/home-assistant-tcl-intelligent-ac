# Physical test: TCL TAC-12CHSD/XA71I

Date: 2026-09-25. Integration: v0.4.6. Home Assistant: 2026.8.3.
One unit, with its owner observing the flap and fan directly. The model was supplied by the owner; the firmware version was not recorded. Reported profile: `if_function=8472704`, `tcl_type=1`.

## Vertical airflow

Tested in Fan mode at low speed, with horizontal airflow set to off.

| Option | `tcl_vdir` | Physical observation |
| --- | --- | --- |
| Full swing | 7 | Up/down movement confirmed |
| Middle fixed | 3 | Flap stopped near the middle |
| Top fixed, then bottom fixed | 1, then 5 | Both distinct end positions confirmed |
| Upper swing | 8 | Movement restricted to the upper range |
| Lower swing | 9 | Movement restricted to the lower range |
| Off | 0 | Movement stopped |

Upper fixed (`2`) and lower fixed (`4`) were not physically tested. The owner reported that this unit has no motorised horizontal function, so horizontal motion was not tested. Earlier command/readback checks accepted horizontal codes; that does not establish the presence of a motor.

## After-run drying: not observed

1. Start Cool at 18°C with low fan speed at 08:20:01 UTC. The owner confirmed clearly cold airflow.
2. Arm the new Anti-mildew switch at 08:22:09. Readback: `pwr=1`, `tcl_mode=3`, `desicmode=1`, `smartdesic=0`.
3. Send normal HA `climate.turn_off` at 08:25:49, approximately 5 minutes 48 seconds after cooling started and 3 minutes 40 seconds after arming.
4. Readback changed to `pwr=0`; `desicmode` remained `1`, including a subsequent poll.
5. The owner observed the fan stop and the flap close. More than two minutes later, both remained stopped/closed. No delayed drying fan operation was observed.

This is a negative result under these conditions, not evidence that every XA71I lacks the feature or that a different field should be substituted. The official app was unavailable, so app synchronisation and a native-app reference cycle were not tested. The cause remains open in [issue #3](https://github.com/jestempablo/home-assistant-tcl-intelligent-ac/issues/3).

The [TCL manual listing this exact model](https://www.electro.pl/products/files/52/5299060/Instrukcja-Obslugi-TCL-Elite-TAC-12CHSD-XA71I.pdf) describes an optional Self-Clean cycle on printed page 18, triggered with CLEAN and shown as CL. No Anti-Mildew after-shutdown procedure was found in this edition. Self-Clean does not establish support for after-run drying; absence from the manual does not establish lack of support either.

The next useful comparison is a native-app Anti-Mildew cycle on the same unit: record `pwr`, `tcl_mode`, `desicmode` and `smartdesic` before arming, after arming, and after shutdown, alongside the physical fan behaviour. Record app/firmware versions if available. This can distinguish an HA/app mismatch from a feature that also fails to run through the native app under the same conditions.

## Restoration

At 08:28:17 UTC, readback confirmed restoration of the original settings: power off, stored Cool mode, 20°C target, high fan, both airflow axes off, `desicmode=0` and `smartdesic=0`. No further hardware cycle was started.
