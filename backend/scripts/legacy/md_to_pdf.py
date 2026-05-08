"""Render a markdown file to a clean PDF using WeasyPrint.

Usage: python3 backend/md_to_pdf.py <input.md> <output.pdf>
"""
import sys
import markdown
from weasyprint import HTML, CSS

CSS_STR = """
@page {
  size: A4;
  margin: 18mm 16mm;
  @bottom-right { content: counter(page) " / " counter(pages); font-size: 9pt; color: #666; }
  @bottom-left  { content: "Compliance Agent · Model Benchmark Briefing"; font-size: 9pt; color: #666; }
}
html { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #1a1a1a; font-size: 10pt; line-height: 1.45; }
body { max-width: 100%; }
h1 { font-size: 20pt; color: #0b5a3e; margin: 16pt 0 10pt; border-bottom: 2px solid #0b5a3e; padding-bottom: 4pt; page-break-after: avoid; }
h2 { font-size: 14pt; color: #0b5a3e; margin: 14pt 0 6pt; page-break-after: avoid; }
h3 { font-size: 11pt; color: #222; margin: 10pt 0 4pt; page-break-after: avoid; }
p  { margin: 4pt 0 6pt; }
a  { color: #0b5a3e; text-decoration: none; }
code { font-family: 'JetBrains Mono', 'SF Mono', ui-monospace, monospace; font-size: 9pt; background: #f2efe8; padding: 1px 4px; border-radius: 3px; }
pre { background: #f2efe8; border: 1px solid #d9d3c6; border-radius: 4px; padding: 8pt 10pt; font-size: 8.5pt; overflow-x: hidden; page-break-inside: avoid; }
pre code { background: transparent; padding: 0; }
table { border-collapse: collapse; margin: 6pt 0 10pt; font-size: 9pt; width: 100%; page-break-inside: avoid; }
th, td { border: 1px solid #d9d3c6; padding: 4pt 6pt; text-align: left; vertical-align: top; }
th { background: #f2efe8; font-weight: 600; font-size: 8.5pt; }
td code { font-size: 8.5pt; }
blockquote { border-left: 3px solid #0b5a3e; margin: 6pt 0; padding: 2pt 10pt; color: #555; background: #f8f6f0; }
strong { color: #111; }
hr { border: none; border-top: 1px solid #d9d3c6; margin: 14pt 0; }
ul, ol { margin: 4pt 0 8pt 18pt; }
li { margin: 2pt 0; }
"""


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: md_to_pdf.py <input.md> <output.pdf>")
        sys.exit(2)

    md_in, pdf_out = sys.argv[1], sys.argv[2]
    with open(md_in) as f:
        md_text = f.read()

    html_body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "md_in_html", "sane_lists", "toc"],
    )
    html_doc = f"<!doctype html><html><head><meta charset='utf-8'></head><body>{html_body}</body></html>"

    HTML(string=html_doc).write_pdf(pdf_out, stylesheets=[CSS(string=CSS_STR)])
    print(f"Wrote {pdf_out}")


if __name__ == "__main__":
    main()
