from utils.source_extractor import _extract_html


def test_html_extraction_preserves_table_rows_and_embedded_chart_values():
    html = """
    <html><head><title>Installed Capacity</title></head><body>
      <main><h2>Annual Capacity</h2><p>Installed capacity expanded over the period.</p></main>
      <section class="data-panel"><table>
        <tr><th>Year</th><th>Capacity (GW)</th></tr>
        <tr><td>2021</td><td>50.1 GW</td></tr>
        <tr><td>2022</td><td>60.2 GW</td></tr>
        <tr><td>2023</td><td>72.3 GW</td></tr>
        <tr><td>2024</td><td>91.2 GW</td></tr>
      </table></section>
      <script>const graph = { labels: ['2021','2022','2023','2024'], datasets: [{label:'Capacity', data:[50.1,60.2,72.3,91.2]}] }</script>
    </body></html>
    """
    extracted = _extract_html(html, max_chars=6000, include_script_data=True)
    assert "[STRUCTURED PUBLISHER TABLE DATA" in extracted
    for point in ("50.1 GW", "60.2 GW", "72.3 GW", "91.2 GW"):
        assert point in extracted
    assert "data:[50.1,60.2,72.3,91.2]" in extracted.replace(" ", "")
