import argparse
import json
import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Project, Tag, LabelingFunction

def export_hinters(project_name: str, output_file: str) -> None:
    """Export all enabled labeling functions for a project to a static JSON configuration."""
    with SessionLocal() as db:
        project = db.scalar(select(Project).where(Project.name == project_name))
        if not project:
            print(f"Error: Project '{project_name}' not found.")
            sys.exit(1)

        tags = list(db.scalars(select(Tag).where(Tag.project_id == project.id)).all())
        if not tags:
            print(f"Error: No tags found for project '{project_name}'.")
            sys.exit(1)

        lfs = list(
            db.scalars(
                select(LabelingFunction).where(
                    LabelingFunction.project_id == project.id,
                    LabelingFunction.enabled == True,
                )
            ).all()
        )

    config = {
        "project_name": project.name,
        "tags": []
    }

    for tag in tags:
        tag_lfs = [lf for lf in lfs if lf.tag_id == tag.id]
        if tag_lfs:
            config["tags"].append({
                "id": tag.id,
                "name": tag.name,
                "lfs": [
                    {
                        "type": lf.type,
                        "config": lf.config
                    }
                    for lf in tag_lfs
                ]
            })

    try:
        with open(output_file, "w") as f:
            json.dump(config, f, indent=2)
        print(f"Successfully exported {len(lfs)} labeling functions across {len(config['tags'])} tags to '{output_file}'")
    except Exception as e:
        print(f"Error writing to {output_file}: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export project labeling functions to a static JSON configuration")
    parser.add_argument("--project-name", required=True, help="Name of the Hinter Factory project")
    parser.add_argument("--output-json", required=True, help="Path to save the output JSON configuration")

    args = parser.parse_args()
    export_hinters(args.project_name, args.output_json)
