# Drone MatrX simulation host (Sprint 5).
#
# Ubuntu 22.04 + Gazebo Harmonic + PX4 SITL (v1.15) + the simhost service
# (SITL process manager, Gazebo control, MAVLink bridge). The Drone MatrX
# backend talks to simhost over HTTP; simulated telemetry goes out on the
# existing NATS subjects (drone.{UIN}.telemetry|state|alert).
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC LANG=C.UTF-8 LC_ALL=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg lsb-release git wget sudo \
        build-essential cmake ninja-build ccache astyle file gdb make rsync unzip zip \
        python3 python3-pip python3-dev python3-venv \
        libxml2-utils xvfb x11-utils ffmpeg mesa-utils libgl1-mesa-dri libegl1 \
        procps iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Gazebo Harmonic (gz-sim 8) from the OSRF repository.
RUN curl -fsSL https://packages.osrfoundation.org/gazebo.gpg -o /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable jammy main" \
        > /etc/apt/sources.list.d/gazebo-stable.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        gz-harmonic python3-gz-transport13 python3-gz-msgs10 \
    && rm -rf /var/lib/apt/lists/*

ARG PX4_TAG=v1.15.4
RUN git clone --branch ${PX4_TAG} --depth 1 https://github.com/PX4/PX4-Autopilot.git /opt/PX4-Autopilot \
    && cd /opt/PX4-Autopilot && git submodule update --init --depth 1 --jobs 4 \
        Tools/simulation/gz src/modules/mavlink/mavlink src/lib/events/libevents src/lib/heatshrink/heatshrink \
        src/drivers/gps/devices src/modules/uxrce_dds_client/Micro-XRCE-DDS-Client
# (No recursive init: NuttX & co. are flight-hardware only. PX4's CMake
#  initialises any further submodule a px4_sitl build needs.)

# Only what PX4's SITL build/code generation needs (the full requirements.txt
# pulls large analysis packages that are not used here).
RUN pip3 install --no-cache-dir --default-timeout=180 --retries 10 \
        empy==3.3.4 jinja2 pyyaml kconfiglib jsonschema pyros-genmsg packaging toml future numpy lxml setuptools

# Nested submodules of the SITL-relevant ones (e.g. mavlink -> pymavlink).
RUN cd /opt/PX4-Autopilot && git submodule update --init --recursive --depth 1 --jobs 4 \
        src/modules/mavlink/mavlink Tools/simulation/gz src/modules/uxrce_dds_client/Micro-XRCE-DDS-Client src/lib/events/libevents

# Build PX4 SITL once (includes the gz_bridge for Gazebo Harmonic).
RUN cd /opt/PX4-Autopilot && make px4_sitl -j"$(nproc)" && rm -rf /root/.ccache

COPY requirements.txt /opt/simhost/requirements.txt
RUN pip3 install --no-cache-dir -r /opt/simhost/requirements.txt

# Sprint 5 PX4 patch: controllable GPS drift in the simulated GPS (see the
# script's docstring). Incremental rebuild: only sensor_gps_sim recompiles.
COPY px4_patches /opt/px4_patches
RUN python3 /opt/px4_patches/gps_drift.py && cd /opt/PX4-Autopilot && make px4_sitl


ENV PX4_ROOT=/opt/PX4-Autopilot \
    PX4_BUILD=/opt/PX4-Autopilot/build/px4_sitl_default \
    GZ_SIM_RESOURCE_PATH=/opt/PX4-Autopilot/Tools/simulation/gz/models:/opt/PX4-Autopilot/Tools/simulation/gz/worlds \
    GZ_IP=127.0.0.1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/simhost
# The simhost code is bind-mounted at runtime (docker-compose.sim.yml) so it
# can share modules with the backend; this COPY is the fallback for CI images.
COPY simhost /opt/simhost/simhost
EXPOSE 8100
CMD ["python3", "-m", "uvicorn", "simhost.server:app", "--host", "0.0.0.0", "--port", "8100"]
