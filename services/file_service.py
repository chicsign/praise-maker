from reportlab.pdfgen import canvas

def make_pdf(images):

    c = canvas.Canvas("setlist.pdf")

    y = 800

    for img in images:
        c.drawImage(img, 100, y, width=400, height=200)
        y -= 220

    c.save()