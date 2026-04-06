"""
Resume Parser - Extracts text from PDF or DOCX resume files.
"""

import os
import logging


def parse_pdf(file_path):
    """Extract text from a PDF file using PyPDF2"""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        return text.strip()
    except ImportError:
        logging.error("PyPDF2 not installed. Run: pip install PyPDF2")
        return None
    except Exception as e:
        logging.error(f"Error parsing PDF: {e}")
        return None


def parse_docx(file_path):
    """Extract text from a DOCX file using python-docx"""
    try:
        from docx import Document
        doc = Document(file_path)
        text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
        return text.strip()
    except ImportError:
        logging.error("python-docx not installed. Run: pip install python-docx")
        return None
    except Exception as e:
        logging.error(f"Error parsing DOCX: {e}")
        return None


def parse_resume(file_path):
    """
    Parse a resume file and return its text content.
    Supports PDF and DOCX formats.
    """
    if not os.path.exists(file_path):
        logging.error(f"Resume file not found: {file_path}")
        return None

    ext = os.path.splitext(file_path)[1].lower()

    if ext == '.pdf':
        text = parse_pdf(file_path)
    elif ext in ['.docx', '.doc']:
        text = parse_docx(file_path)
    elif ext == '.txt':
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
    else:
        logging.error(f"Unsupported resume format: {ext}. Use PDF, DOCX, or TXT.")
        return None

    if text:
        # Clean up the text
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        text = '\n'.join(lines)
        logging.info(f"✅ Resume parsed: {len(text)} characters, {len(lines)} lines")
    else:
        logging.error("Failed to extract text from resume")

    return text


# ============ STANDALONE TEST ============
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    # Test with the resume file
    test_file = sys.argv[1] if len(sys.argv) > 1 else "Priyesh-Raj-Singh-cv.pdf"
    print(f"\nParsing: {test_file}")
    print("=" * 60)

    result = parse_resume(test_file)
    if result:
        print(result)
        print("=" * 60)
        print(f"\nTotal: {len(result)} characters")
    else:
        print("❌ Failed to parse resume")
