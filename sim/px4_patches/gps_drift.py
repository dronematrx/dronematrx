"""Drone MatrX Sprint 5 patch for PX4 v1.15 SITL: controllable GPS drift.

PX4 v1.15 with Gazebo produces the GPS measurement in `sensor_gps_sim`
(ground truth + white noise). Its failure-injection command for GPS is only
implemented in the legacy simulator_mavlink path, so there is no way to bias
the GPS fed to the estimator. This patch adds two parameters:

    SIM_GPS_DRIFT_N   metres added northwards to the simulated GPS position
    SIM_GPS_DRIFT_E   metres added eastwards

Defaults are 0 (behaviour unchanged). Set at runtime over MAVLink PARAM_SET
by the simhost "gps_drift" failure; the true Gazebo position is unaffected,
so true and reported positions stay distinguishable.
"""
from pathlib import Path

ROOT = Path("/opt/PX4-Autopilot/src/modules/simulation/sensor_gps_sim")

cpp = ROOT / "SensorGpsSim.cpp"
src = cpp.read_text()
old = "\t\tdouble altitude = (double)(gpos.alt + (generate_wgn() * 0.5f));\n"
new = (
    old
    + "\t\t// Drone MatrX: controlled GPS drift (estimator input only).\n"
    + "\t\tlatitude += math::degrees((double)_sim_gps_drift_n.get() / CONSTANTS_RADIUS_OF_EARTH);\n"
    + "\t\tlongitude += math::degrees((double)_sim_gps_drift_e.get() / (CONSTANTS_RADIUS_OF_EARTH * cos(math::radians(gpos.lat))));\n"
)
assert src.count(old) == 1, "SensorGpsSim.cpp layout changed"
cpp.write_text(src.replace(old, new))

hpp = ROOT / "SensorGpsSim.hpp"
src = hpp.read_text()
old = "\t\t(ParamInt<px4::params::SIM_GPS_USED>) _sim_gps_used\n"
new = (
    "\t\t(ParamInt<px4::params::SIM_GPS_USED>) _sim_gps_used,\n"
    "\t\t(ParamFloat<px4::params::SIM_GPS_DRIFT_N>) _sim_gps_drift_n,\n"
    "\t\t(ParamFloat<px4::params::SIM_GPS_DRIFT_E>) _sim_gps_drift_e\n"
)
assert src.count(old) == 1, "SensorGpsSim.hpp layout changed"
hpp.write_text(src.replace(old, new))

params = ROOT / "parameters.c"
params.write_text(
    params.read_text()
    + """

/**
 * Simulated GPS drift, north (Drone MatrX failure injection)
 *
 * @unit m
 * @min -500
 * @max 500
 * @decimal 1
 * @group Simulator
 */
PARAM_DEFINE_FLOAT(SIM_GPS_DRIFT_N, 0.0f);

/**
 * Simulated GPS drift, east (Drone MatrX failure injection)
 *
 * @unit m
 * @min -500
 * @max 500
 * @decimal 1
 * @group Simulator
 */
PARAM_DEFINE_FLOAT(SIM_GPS_DRIFT_E, 0.0f);
"""
)
print("gps drift patch applied")
