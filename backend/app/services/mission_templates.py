"""Built-in mission templates (wizard Step 1). A template pre-fills the
flight parameters and sensor requirements of later steps; the operator can
still change every value."""
from app.schemas.mission_planning import TemplateOut

TEMPLATES: dict[str, TemplateOut] = {
    t.key: t
    for t in [
        TemplateOut(
            key="survey",
            label="Area Survey",
            description="Systematic lawnmower photo survey of the AOI.",
            mission_type="SURVEY",
            flight={"altitude_m": 60, "speed_mps": 10, "overlap_pct": 70, "pattern": "lawnmower", "priority": 2},
            required_sensors=["camera"],
        ),
        TemplateOut(
            key="search",
            label="Search & Rescue",
            description="AOI divided into search cells, each swept with a thermal creeping-line pattern.",
            mission_type="SAR",
            flight={"altitude_m": 40, "speed_mps": 8, "overlap_pct": 30, "pattern": "sar_cells", "priority": 4},
            required_sensors=["thermal"],
        ),
        TemplateOut(
            key="inspection",
            label="Infrastructure Inspection",
            description="Route connecting the inspection targets picked in step 5 (coverage pass if none).",
            mission_type="INSPECT",
            flight={"altitude_m": 30, "speed_mps": 5, "overlap_pct": 80, "pattern": "lawnmower", "priority": 3},
            required_sensors=["camera"],
        ),
        TemplateOut(
            key="mapping",
            label="Mapping / Photogrammetry",
            description="Crosshatch pattern for orthomosaic and 3D reconstruction.",
            mission_type="SURVEY",
            flight={"altitude_m": 80, "speed_mps": 12, "overlap_pct": 75, "pattern": "crosshatch", "priority": 2},
            required_sensors=["camera"],
        ),
        TemplateOut(
            key="agriculture",
            label="Agriculture Survey",
            description="Parallel crop rows at a fixed lane spacing (multispectral / spraying).",
            mission_type="AGRI",
            flight={"altitude_m": 30, "speed_mps": 6, "overlap_pct": 0, "pattern": "lawnmower", "lane_spacing_m": 15, "priority": 2},
            required_sensors=["multispectral"],
        ),
        TemplateOut(
            key="custom",
            label="Custom",
            description="Ordered custom waypoints (step 5), or neutral coverage defaults.",
            mission_type="OTHER",
            flight={"altitude_m": 60, "speed_mps": 10, "overlap_pct": 60, "pattern": "lawnmower", "priority": 2},
            required_sensors=["camera"],
        ),
    ]
}
