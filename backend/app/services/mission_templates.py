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
            description="Fast, low-overlap thermal sweep to locate people or heat sources.",
            mission_type="SAR",
            flight={"altitude_m": 40, "speed_mps": 8, "overlap_pct": 30, "pattern": "lawnmower", "priority": 4},
            required_sensors=["thermal"],
        ),
        TemplateOut(
            key="inspection",
            label="Infrastructure Inspection",
            description="Low, slow, high-overlap pass for close visual inspection.",
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
            key="custom",
            label="Custom",
            description="Start from neutral defaults and configure everything yourself.",
            mission_type="OTHER",
            flight={"altitude_m": 60, "speed_mps": 10, "overlap_pct": 60, "pattern": "lawnmower", "priority": 2},
            required_sensors=["camera"],
        ),
    ]
}
