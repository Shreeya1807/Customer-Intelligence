"""Generate a native PBIP report with an embedded synthetic-data snapshot."""

import base64
import json
import zlib
from pathlib import Path

import pandas as pd

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/"


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n")


def field(table, name, measure=False):
    kind = "Measure" if measure else "Column"
    return {kind: {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}


def projection(table, name, measure=False):
    return {
        "field": field(table, name, measure),
        "queryRef": f"{table}.{name}",
        "nativeQueryRef": name,
    }


def build(artifacts="artifacts", output="powerbi/CustomerIntelligence"):
    artifacts, output = Path(artifacts), Path(output)
    report = json.loads((artifacts / "metrics.json").read_text())
    if not report["data_source"].startswith("synthetic"):
        raise ValueError(
            "Native snapshot export only accepts the synthetic demonstration data"
        )
    frames = {
        name: pd.read_csv(artifacts / f"{name}.csv")
        for name in ["customers", "monthly_revenue", "retention"]
    }
    frames["evaluation"] = pd.DataFrame(
        [
            {
                "model": name,
                **{k: v for k, v in report[key].items() if k != "confusion_matrix"},
            }
            for name, key in [
                ("Logistic regression", "model"),
                ("Prior baseline", "baseline"),
            ]
        ]
    )
    measures = {
        "customers": [
            ("Customer Count", "DISTINCTCOUNT(customers[customer_id])", "#,0"),
            ("Observed Revenue", "SUM(customers[monetary])", "$#,0"),
            (
                "Average Order Value",
                "DIVIDE(SUM(customers[monetary]),SUM(customers[frequency]))",
                "$0.00",
            ),
            (
                "Above Reference Threshold",
                f"CALCULATE(COUNTROWS(customers),customers[risk]>={report['threshold']})",
                "#,0",
            ),
            (
                "Data notice",
                '"SYNTHETIC DATA | 60-day inactivity proxy | No real business impact established"',
                None,
            ),
        ],
        "monthly_revenue": [
            ("Monthly Revenue", "SUM(monthly_revenue[revenue])", "$#,0")
        ],
        "retention": [("Retention Rate", "AVERAGE(retention[retention])", "0.0%")],
        "evaluation": [],
    }
    tables = []
    for name, data in frames.items():
        columns, types = [], []
        for col in data.columns:
            integer = pd.api.types.is_integer_dtype(data[col])
            numeric = pd.api.types.is_numeric_dtype(data[col])
            kind = "int64" if integer else "double" if numeric else "string"
            columns.append(
                {
                    "name": col,
                    "dataType": kind,
                    "sourceColumn": col,
                    "summarizeBy": "none",
                }
            )
            types.append(
                "{"
                + json.dumps(col)
                + ", "
                + (
                    "Int64.Type"
                    if integer
                    else "type number"
                    if numeric
                    else "type text"
                )
                + "}"
            )
        rows = data.astype(str).values.tolist()
        compressor = zlib.compressobj(wbits=-15)
        payload = base64.b64encode(
            compressor.compress(json.dumps(rows).encode()) + compressor.flush()
        ).decode()
        m = [
            "let",
            f'  Rows = Json.Document(Binary.Decompress(Binary.FromText("{payload}", BinaryEncoding.Base64), Compression.Deflate)),',
            "  Source = Table.FromRows(Rows, {"
            + ",".join(json.dumps(c) for c in data.columns)
            + "}),",
            "  Typed = Table.TransformColumnTypes(Source, {"
            + ",".join(types)
            + '}, "en-US")',
            "in Typed",
        ]
        table = {
            "name": name,
            "columns": columns,
            "partitions": [
                {
                    "name": name,
                    "mode": "import",
                    "source": {"type": "m", "expression": m},
                }
            ],
        }
        if measures[name]:
            table["measures"] = [
                dict(name=n, expression=e, **({"formatString": f} if f else {}))
                for n, e, f in measures[name]
            ]
        tables.append(table)
    model_dir = output / "CustomerIntelligence.SemanticModel"
    report_dir = output / "CustomerIntelligence.Report"
    write(
        model_dir / "model.bim",
        {
            "name": "CustomerIntelligence",
            "compatibilityLevel": 1567,
            "model": {
                "culture": "en-US",
                "defaultPowerBIDataSourceVersion": "powerBI_V3",
                "tables": tables,
            },
        },
    )
    write(
        model_dir / "definition.pbism",
        {
            "$schema": SCHEMA + "semanticModel/definitionProperties/1.0.0/schema.json",
            "version": "1.0",
            "settings": {},
        },
    )
    write(
        report_dir / "definition.pbir",
        {
            "$schema": SCHEMA + "report/definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {
                "byPath": {"path": "../CustomerIntelligence.SemanticModel"}
            },
        },
    )
    write(
        output / "CustomerIntelligence.pbip",
        {
            "version": "1.0",
            "artifacts": [{"report": {"path": "CustomerIntelligence.Report"}}],
            "settings": {"enableAutoRecovery": True},
        },
    )
    definition = report_dir / "definition"
    write(
        definition / "version.json",
        {
            "$schema": SCHEMA + "report/definition/versionMetadata/1.0.0/schema.json",
            "version": "2.0.0",
        },
    )
    write(
        definition / "report.json",
        {
            "$schema": SCHEMA + "report/definition/report/3.1.0/schema.json",
            "themeCollection": {},
        },
    )
    pages = ["overview", "retention", "evaluation"]
    write(
        definition / "pages/pages.json",
        {
            "$schema": SCHEMA + "report/definition/pagesMetadata/1.0.0/schema.json",
            "pageOrder": pages,
            "activePageName": "overview",
        },
    )

    def visual(page, name, kind, roles, x, y, width, height):
        write(
            definition / f"pages/{page}/visuals/{name}/visual.json",
            {
                "$schema": SCHEMA
                + "report/definition/visualContainer/2.4.0/schema.json",
                "name": name,
                "position": {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "z": y + x,
                    "tabOrder": y + x,
                },
                "visual": {
                    "visualType": kind,
                    "query": {
                        "queryState": {r: {"projections": p} for r, p in roles.items()}
                    },
                    "drillFilterOtherVisuals": True,
                },
            },
        )

    for page in pages:
        write(
            definition / f"pages/{page}/page.json",
            {
                "$schema": SCHEMA + "report/definition/page/1.0.0/schema.json",
                "name": page,
                "displayName": page.title() + " · Synthetic sample",
                "displayOption": "FitToPage",
                "width": 1280,
                "height": 800,
            },
        )
        visual(
            page,
            "notice",
            "cardVisual",
            {"Data": [projection("customers", "Data notice", True)]},
            20,
            10,
            1240,
            70,
        )
    for index, measure in enumerate(
        [
            "Customer Count",
            "Observed Revenue",
            "Above Reference Threshold",
            "Average Order Value",
        ]
    ):
        visual(
            "overview",
            f"kpi{index}",
            "cardVisual",
            {"Data": [projection("customers", measure, True)]},
            20 + index * 310,
            90,
            300,
            140,
        )
    visual(
        "overview",
        "revenue",
        "lineChart",
        {
            "Category": [projection("monthly_revenue", "month")],
            "Y": [projection("monthly_revenue", "Monthly Revenue", True)],
        },
        20,
        250,
        800,
        350,
    )
    visual(
        "overview",
        "segments",
        "clusteredBarChart",
        {
            "Category": [projection("customers", "segment")],
            "Y": [projection("customers", "Observed Revenue", True)],
        },
        840,
        250,
        420,
        350,
    )
    visual(
        "overview",
        "segmentFilter",
        "slicer",
        {"Values": [projection("customers", "segment")]},
        20,
        620,
        1240,
        150,
    )
    visual(
        "retention",
        "cohorts",
        "pivotTable",
        {
            "Rows": [projection("retention", "cohort")],
            "Columns": [projection("retention", "month")],
            "Values": [projection("retention", "Retention Rate", True)],
        },
        20,
        100,
        1240,
        650,
    )
    visual(
        "evaluation",
        "metrics",
        "tableEx",
        {"Values": [projection("evaluation", c) for c in frames["evaluation"].columns]},
        20,
        100,
        1240,
        200,
    )
    visual(
        "evaluation",
        "review",
        "tableEx",
        {
            "Values": [
                projection("customers", c)
                for c in [
                    "customer_id",
                    "segment",
                    "cluster",
                    "risk",
                    "evaluation_split",
                ]
            ]
        },
        20,
        330,
        1240,
        420,
    )
    return output


if __name__ == "__main__":
    print(f"Generated native project: {build()}")
