"""Sprint 5: Gazebo + PX4 SITL mission simulation.

    geo        WGS84 <-> local ENU frame shared by Leaflet, the planner and Gazebo
    dem        elevation sources (AWS Terrain Tiles, fixtures, flat fallback)
    world      AOI -> per-session Gazebo world (heightmap + SDF + metadata)
    fleet      simulation drones and their spawn poses
    planners   task-type specific task generation (survey/SAR/agri/inspection/custom)
    adapters   flight-controller adapter interface (simulation | hardware)
    simhost    HTTP client for the simulation host (SITL manager + Gazebo + MAVLink)
    orchestrator  session lifecycle, progress tracking, failure responses
    reports    simulation report + comparison
"""
