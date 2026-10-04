"""SDMA action report (PDF): zone map, KPIs, AI/template briefing, priority list, relocation plan."""
import io
from datetime import datetime
from xml.sax.saxutils import escape as esc

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # noqa: E402

ZONE_COLORS = {"red": "#d7301f", "orange": "#fc8d59", "yellow": "#fdd49e", "green": "#a6d96a"}


def _map_png(cells, habs, sites):
    fig, ax = plt.subplots(figsize=(7, 6), dpi=130)
    for z, col in ZONE_COLORS.items():
        c = cells[cells.zone == z]
        ax.scatter(c.lon, c.lat, s=0.4, c=col, label=f"{z.title()} zone", linewidths=0)
    imm = habs[habs.priority == "immediate"]
    ax.scatter(imm.lon, imm.lat, s=14, c="black", marker="^", label="Immediate relocation")
    ax.scatter(sites.lon, sites.lat, s=12, c="#2166ac", marker="s", label="Safe site")
    ax.set_aspect("equal")
    ax.legend(loc="lower left", fontsize=7, markerscale=4, frameon=True)
    ax.set_xticks([]), ax.set_yticks([])
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _table(rows, widths):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")])]))
    return t


def build_pdf(facts, brief, cells, habs, sites, plan) -> bytes:
    ss = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.5 * cm)
    z = facts["zones"]
    story = [
        Paragraph(f"SURAKSHA – Red Zone & Relocation Action Report", ss["Title"]),
        Paragraph(f"{facts['region']} · generated {datetime.now():%d %b %Y %H:%M}", ss["Normal"]),
        Spacer(1, 8),
        _table([["Red cells", "Orange", "Pop. in Red", "Immediate", "Short-term", "Medium-term", "Sites (capacity)"],
                [f"{z['red']:,}", f"{z['orange']:,}", f"{facts['population_in_red']:,}",
                 f"{facts['counts'].get('immediate', 0)} ({facts['people'].get('immediate', 0):,})",
                 f"{facts['counts'].get('short', 0)} ({facts['people'].get('short', 0):,})",
                 f"{facts['counts'].get('medium', 0)} ({facts['people'].get('medium', 0):,})",
                 f"{facts['sites']} ({facts['site_capacity']:,})"]], [2.5 * cm] * 7),
        Spacer(1, 8),
        Image(_map_png(cells, habs, sites), width=16 * cm, height=13.5 * cm),
        Paragraph(f"Briefing <font size=7>({esc(brief['source'])})</font>", ss["Heading2"]),
        Paragraph(f"<b>{esc(brief['headline'])}</b>", ss["Normal"]),
        Spacer(1, 4),
    ]
    for title, key in (("Situation", "situation"), ("Relocation sites & plan", "sites"), ("Weather & alerts", "weather")):
        story.append(Paragraph(title, ss["Heading4"]))
        story += [Paragraph(f"• {esc(s)}", ss["Normal"]) for s in brief[key]]
    story.append(Paragraph("Recommended actions", ss["Heading4"]))
    story.append(_table([["When", "Action"]] + [[esc(a.get("when", "")), Paragraph(esc(a.get("action", "")), ss["BodyText"])]
                                                 for a in brief["actions"]], [3 * cm, 15 * cm]))
    story.append(PageBreak())
    story.append(Paragraph("Habitations prioritised for relocation", ss["Heading2"]))
    top = habs[habs.priority.isin(["immediate", "short"])].sort_values(["priority", "risk"], ascending=[True, False]).head(40)
    story.append(_table([["Habitation", "District", "Pop.", "Priority", "Risk", "Key reasons"]] +
                        [[h.name[:28], h.district, f"{h.pop:,.0f}", h.priority, f"{h.risk:.2f}",
                          Paragraph("; ".join(h.reasons[:2]), ss["BodyText"])] for h in top.itertuples()],
                        [4 * cm, 2.3 * cm, 1.4 * cm, 1.8 * cm, 1.2 * cm, 7.3 * cm]))
    story.append(Paragraph("Relocation plan (optimised allocation)", ss["Heading2"]))
    names = habs.set_index("hab_id").name
    s = sites.set_index("site_id")
    rows = [["Habitation", "→ Site", "Persons", "Distance (km)", "Site capacity", "Limiting factor"]]
    for p in plan.head(40).itertuples():
        site = s.loc[p.site_id] if isinstance(p.site_id, str) and p.site_id in s.index else None
        rows.append([str(names.get(p.hab_id, p.hab_id))[:28], p.site_id or "UNASSIGNED – no capacity within 40 km",
                     f"{p.persons:,}", "" if p.distance_km is None else f"{p.distance_km}",
                     "" if site is None else f"{site.capacity:,}", "" if site is None else site.limiting_factor])
    story.append(_table(rows, [4 * cm, 4.6 * cm, 1.8 * cm, 2.2 * cm, 2.2 * cm, 2.6 * cm]))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Method: multi-hazard index (AHP weights) over H3 cells; landslide susceptibility from "
                           "XGBoost trained on the GSI inventory (spatial CV); carrying capacity = min(land, water @55 LPCD, "
                           "services); allocation by mixed-integer optimisation keeping communities together. "
                           "Decision-support output: verify on ground before action.", ss["Italic"]))
    doc.build(story)
    return buf.getvalue()
