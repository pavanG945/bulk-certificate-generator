from datetime import date

from fpdf import FPDF

# Single predefined template: landscape A4, double border, centred text.
_PAGE_W, _PAGE_H = 297, 210


def generate_certificate_pdf(*, recipient_name: str, title: str, issuer: str, issue_date: date) -> bytes:
    """Render the certificate template and return the PDF bytes.

    Uses the built-in Helvetica font, so names outside Latin-1 raise an error.
    The caller records that as a failure of this one certificate.
    """
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    pdf.set_draw_color(30, 58, 138)
    pdf.set_line_width(2)
    pdf.rect(8, 8, _PAGE_W - 16, _PAGE_H - 16)
    pdf.set_line_width(0.5)
    pdf.rect(12, 12, _PAGE_W - 24, _PAGE_H - 24)

    def line(text: str, y: float, size: int, style: str = "", color=(0, 0, 0)):
        pdf.set_font("Helvetica", style, size)
        pdf.set_text_color(*color)
        pdf.set_xy(20, y)
        pdf.cell(_PAGE_W - 40, size * 0.5, text, align="C")

    line("CERTIFICATE OF COMPLETION", 40, 34, "B", (30, 58, 138))
    line("This is to certify that", 75, 16)
    line(recipient_name, 90, 30, "B")
    line("has successfully completed", 115, 16)
    line(title, 128, 22, "B", (30, 58, 138))
    line(f"Issued by {issuer}", 165, 14)
    line(f"Date: {issue_date.strftime('%d %B %Y')}", 175, 12)

    return bytes(pdf.output())
