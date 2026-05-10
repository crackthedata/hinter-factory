import argparse
import sys
from typing import Any

import polars as pl
from sqlalchemy import select

from app.database import SessionLocal
from app.lf_executor import execute_labeling_function, LfConfigError
from app.models import Project, Tag, LabelingFunction
from app.probabilistic_aggregator import aggregate_one


def process_csv(
    project_name: str,
    input_csv: str,
    output_csv: str,
    text_column: str,
) -> None:
    """Run labeling functions for a project on a CSV file and output probabilities."""
    # 1. Connect to DB and fetch configuration
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

    # Group LFs by tag
    lfs_by_tag: dict[str, list[LabelingFunction]] = {tag.id: [] for tag in tags}
    for lf in lfs:
        if lf.tag_id in lfs_by_tag:
            lfs_by_tag[lf.tag_id].append(lf)

    # 2. Read CSV
    try:
        df = pl.read_csv(input_csv)
    except Exception as e:
        print(f"Error reading input CSV '{input_csv}': {e}")
        sys.exit(1)

    if text_column not in df.columns:
        print(f"Error: Text column '{text_column}' not found in CSV. Available columns: {df.columns}")
        sys.exit(1)

    # 3. Process each row
    texts = df[text_column].to_list()
    
    # We will build a list of probabilities for each tag
    results: dict[str, list[float]] = {tag.name: [] for tag in tags}

    for text in texts:
        text_str = str(text) if text is not None else ""
        
        for tag in tags:
            pos_votes = 0
            neg_votes = 0
            
            for lf in lfs_by_tag[tag.id]:
                try:
                    vote = execute_labeling_function(lf.type, lf.config, text_str)
                    if vote > 0:
                        pos_votes += 1
                    elif vote < 0:
                        neg_votes += 1
                except LfConfigError:
                    # In a batch job, we might want to log this, but we'll continue
                    continue
            
            prob, _, _ = aggregate_one(pos_votes, neg_votes)
            results[tag.name].append(prob)

    # 4. Append results to DataFrame and save
    for tag_name, probs in results.items():
        df = df.with_columns(pl.Series(f"{tag_name}_probability", probs))

    try:
        df.write_csv(output_csv)
        print(f"Successfully wrote results to '{output_csv}'")
    except Exception as e:
        print(f"Error writing output CSV '{output_csv}': {e}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run headless LF batch processing on a CSV")
    parser.add_argument("--project-name", required=True, help="Name of the Hinter Factory project")
    parser.add_argument("--input-csv", required=True, help="Path to input CSV file")
    parser.add_argument("--output-csv", required=True, help="Path to save the output CSV file")
    parser.add_argument("--text-column", required=True, help="Name of the column containing the text")

    args = parser.parse_args()
    process_csv(args.project_name, args.input_csv, args.output_csv, args.text_column)
