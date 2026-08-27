"""Report generation module for Taiwan stock analysis."""

import pandas as pd  # noqa: F401
from datetime import datetime

from .charts import plot_interactive_kline, plot_technical


def generate_report(stock_id: str, df: dict, output_path: str, format: str = "html") -> str:
    """Generate full analysis report as HTML or PNG.

    Args:
        stock_id: Stock ticker / ID string (e.g. "2330").
        df: Dict mapping data names to DataFrames.
            Expected key 'technical' with a DataFrame.
        output_path: Destination file path for the report.
        format: Output format ("html" or "png").

    Returns:
        The output_path.
    """
    tech_df = df.get("technical", pd.DataFrame())

    if format == "html":
        html = plot_interactive_kline(tech_df, stock_id)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
    elif format == "png":
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        fig = plot_technical(tech_df)
        fig.update_layout(title=f"{stock_id} Analysis Report - Generated {now_str}")
        fig.write_image(output_path)

    return output_path
