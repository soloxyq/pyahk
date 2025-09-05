# Initialize PaddleOCR instance
from paddleocr import PaddleOCR
ocr = PaddleOCR(
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False)

# Run OCR inference on a sample image 
result = ocr.predict(
    input="f7_screenshot_20250810_115106.png")

# Visualize the results and save the JSON resultspy
for res in result:
    res.print()
    res.save_to_img("output")
    res.save_to_json("output")
ocr.export_paddlex_config_to_yaml("PaddleOCR.yaml")