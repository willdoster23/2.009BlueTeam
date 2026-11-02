# Four-winch wall robot simulator

Four cables run from anchors at (or beyond) the corners of a flat wall to the corners of the robot chassis. For every point on the wall, the simulator computes:

- **Cable tensions.** The slackest cable is held at the pretension and the other three are solved from force and moment balance.
- **Usable wall.** Points are dropped where the chassis hits the edge margin, where the cables can't all be tensioned, or where a cable would exceed the tension limit.
- **Winch power** at max speed, plus **holding power** (motor winding loss just to hold tension).
- **Energy to cover the whole wall** in vertical passes, and how many 10 lb battery packs that takes.

## Run

```
cd simulations/fourwinch
python wall_winch_sim.py               # saves tension_map.png + power_map.png, opens plots
python wall_winch_sim.py --no-show     # just save the plots
python wall_winch_sim.py --point 30 50 # tensions and power at one location
python wall_winch_sim.py --help        # all options
```

Defaults are set in the `Config` class at the top of `wall_winch_sim.py`. Edit them there, or override any of them on the command line.

Useful options: `--tmax`, `--tpre`, `--chassis W H`, `--anchor-top`, `--anchor-side`, `--speed`, `--swath`, `--km`, `--spool-radius`, `--battery-wh`.
