"""
Visualization module for fiscal data Sankey diagrams.
"""

from typing import Dict, List

import plotly.graph_objects as go


def sankey_figure(
    title: str, 
    receipts: Dict[str, float], 
    outlays: Dict[str, float], 
    fiscal_period: str = ""
) -> go.Figure:
    """
    Generate a Sankey diagram visualization of fiscal flows.
    
    Args:
        title: Main title for the chart
        receipts: Dictionary of {category: amount_billions}
        outlays: Dictionary of {category: amount_billions}
        fiscal_period: Optional label for the fiscal period
    
    Returns:
        Plotly Figure object ready for export
    """
    total_r = sum(receipts.values())
    total_o = sum(outlays.values())
    balance = total_r - total_o
    deficit = -balance if balance < 0 else 0.0
    surplus = balance if balance > 0 else 0.0

    nodes: List[str] = []
    node_colors: List[str] = []
    idx: Dict[str, int] = {}

    def add_node(name: str, color: str = "lightgray") -> int:
        if name in idx:
            return idx[name]
        idx[name] = len(nodes)
        nodes.append(name)
        node_colors.append(color)
        return idx[name]

    # Central node
    total_node = add_node(
        f"<b>Total Receipts</b><br>${total_r:.1f}B<br><br><b>Total Outlays</b><br>${total_o:.1f}B", 
        "#FFE5B4"
    )
    
    src: List[int] = []
    tgt: List[int] = []
    val: List[float] = []
    colors: List[str] = []
    labels: List[str] = []

    # Receipts flow (green shades)
    receipt_colors = ["#90EE90", "#7CCD7C", "#68B068", "#54A354", "#408940", "#2C6F2C"]
    for i, (k, v) in enumerate(receipts.items()):
        rn = add_node(k, receipt_colors[i % len(receipt_colors)])
        src.append(rn)
        tgt.append(total_node)
        val.append(v)
        colors.append(receipt_colors[i % len(receipt_colors)])
        labels.append(f"${v:.1f}B")

    # Deficit (if present)
    if deficit > 0.01:
        dn = add_node("<b>Deficit</b>", "#FF6B6B")
        src.append(dn)
        tgt.append(total_node)
        val.append(deficit)
        colors.append("#FF6B6B")
        labels.append(f"${deficit:.1f}B")

    # Outlays flow (blue/teal shades)
    outlay_colors = ["#4682B4", "#5F9EA0", "#48D1CC", "#20B2AA", "#008B8B", "#00CED1", "#4169E1", "#6495ED"]
    for i, (k, v) in enumerate(outlays.items()):
        on = add_node(k, outlay_colors[i % len(outlay_colors)])
        src.append(total_node)
        tgt.append(on)
        val.append(v)
        colors.append(outlay_colors[i % len(outlay_colors)])
        labels.append(f"${v:.1f}B")

    # Surplus (if present)
    if surplus > 0.01:
        sn = add_node("<b>Surplus</b>", "#90EE90")
        src.append(total_node)
        tgt.append(sn)
        val.append(surplus)
        colors.append("#90EE90")
        labels.append(f"${surplus:.1f}B")

    fig = go.Figure(data=[go.Sankey(
        arrangement="snap",
        node=dict(
            pad=20,
            thickness=20,
            line=dict(color="white", width=2),
            label=nodes,
            color=node_colors,
        ),
        link=dict(
            source=src,
            target=tgt,
            value=val,
            color=colors,
            label=labels,
        ),
    )])
    
    # Build subtitle with totals
    subtitle = f"Total Receipts: ${total_r:.1f}B | Total Outlays: ${total_o:.1f}B"
    if deficit > 0.01:
        subtitle += f" | Deficit: ${deficit:.1f}B"
    elif surplus > 0.01:
        subtitle += f" | Surplus: ${surplus:.1f}B"
    
    fig.update_layout(
        title_text=f"{title} — {subtitle}",
        title_font_size=13,
        font_size=11,
        height=700,
        margin=dict(l=20, r=20, t=140, b=20),
    )
    
    # Add fiscal period annotation
    if fiscal_period:
        fig.add_annotation(
            text=f"Data Period: {fiscal_period}",
            xref="paper", yref="paper",
            x=0.5, y=1.08,
            showarrow=False,
            font=dict(size=12, color="#444444"),
            xanchor="center", yanchor="top"
        )
    
    return fig
