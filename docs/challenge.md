# Challenge note: Hindi legacy-font tables and mixed-format agricultural documents

## 1. The challenge
Haryana's district statistical abstracts are the richest district-level source in our inventory (tehsil and block tables for land use, crops, irrigation, livestock, prices), but 20 of the 41 sources are Hindi PDFs. Opening one real file (Bhiwani, 98 pages) showed three things the inventory did not say:
1. **They are not scanned.** Every page has a text layer, but the Hindi is in the legacy **Kruti Dev** font, so the text reads like `ftyk lakf[;dh; lkjka'k` (Latin letters; only the glyphs are Hindi). 58 of 98 pages are like this; 40 are English.
2. **Numbers and Hindi behave differently.** Digits survive the encoding, but this font prints a decimal point as a hyphen (`34-79` = 34.79) and lays out pages as mixed Hindi labels, English tehsil names and numbers.
3. **Wrong is worse than missing.** A row with the wrong number of cells, a unit read from the wrong column, or an OCR digit slip silently becomes a wrong fact in the database.

So the question for the Hindi agent is not "which OCR is best" but **"when is OCR needed at all, and how do we avoid guessing when it is?"**

## 2. What the literature and tool documentation cover
*(This section is for the team to finish. The items below are what we could state; verify each reference before citing.)*
- **Tesseract** is an open-source OCR engine with trained models per language, including Hindi (`hin`); R. Smith, "An Overview of the Tesseract OCR Engine", ICDAR 2007.
- **PaddleOCR** provides detection plus recognition models for many scripts, including Devanagari (PaddleOCR 3.0 technical report, 2025, arXiv:2507.05595).
- **Docling** (IBM, 2024, arXiv:2408.09869) converts documents with layout and table recognition. *Not run by us.*
- **Gap we could not find covered:** none of the above says anything about legacy-font PDFs whose text layer is already present but encoded wrongly. This is an engineering fact about the files (the font maps Hindi glyphs onto Latin code points), not a published result we can cite.

**TEAM TO ADD:** the papers or reports you read on table extraction from statistical publications and on Indic OCR, and one sentence each on what they do and do not cover.

## 3. Tools actually run on our sources, with numbers
| # | Tool | On what | Result | Evidence |
|---|---|---|---|---|
| 1 | pdfplumber text layer, raw | Hisar abstract p.15 vs a hand-typed Unicode reference | CER 0.73; digit recall 0.83, precision 0.67 (the text is legacy-encoded, so CER is meaningless for Hindi letters; the digits are what matters) | `ocr_results.csv` |
| 2 | Tesseract (hin+eng) | same page | CER 0.50; digit recall 0.50, precision 0.44 | `ocr_results.csv` |
| 3 | EasyOCR (hi+en) | same page | CER 0.51; digit recall 0.84, precision 0.88; 55 s per page on CPU | `ocr_results.csv` |
| 4 | Kruti Dev decoder (ours, fixed rules) + pdfplumber | Bhiwani, all 98 pages | 4,820 rows loaded, 184 rows rejected, 29 pages skipped; 14 of 14 values on page 8 match the rendered page by eye | run 4, `test_hindi.py` |
| 5 | Tesseract (hin+eng, grid lines removed, digit pass) | 5 Bhiwani pages turned into images, compared with the text-layer result | recall 0.58, precision 0.78 (0.81 to 1.00 on item/unit/district/state pages; 0.00 on the tehsil grid page) | `eval/ocr_backend_comparison.csv` |
| 6 | PaddleOCR 3.7 | same pages | **not run**: installed, but its Devanagari model cannot be downloaded in our environment (all model hosts unreachable) | `eval/ocr_backend_comparison.csv` |

Reproduce 4 and 5: `python run.py --input data/raw/Bhiwani.pdf --source-name "District Statistical Abstract: Bhiwani"` and `python tools/ocr_backend_eval.py data/raw/Bhiwani.pdf "Haryana DESA - District Statistical Abstract: Bhiwani"`.

## 4. What we decided and why
- **Decode before OCR.** Where a text layer exists we decode the legacy font with a fixed table (`kruti_dev.py`) and copy digits from the PDF. It was both more complete and far cheaper than the OCR we tried: the whole 98-page document decodes in about 4 seconds on a CPU (tool 4), while EasyOCR needed 55 seconds for a single page (tool 3).
- **OCR only as a fallback** (pages with no text layer), through a backend chain (Paddle, then Tesseract, then EasyOCR). Rows from OCR are stored with confidence 0.6 and flagged for review.
- **Never guess:** a row with the wrong number of values is rejected and counted; an unknown unit is stored as `unspecified` and flagged, not invented.
- **Alternatives considered:** an LLM to read the page images (not tried: cost, and it cannot be checked against a fixed rule); layout models such as Docling (not run).

## 5. Limits: research versus our implementation
**Research / source limits** (true whatever we build):
- OCR of Devanagari tables is weak: our best measured recall on image-only pages was 0.58 (Tesseract) with 0.84 digit recall for EasyOCR on a single page.
- A decimal printed as a hyphen is ambiguous in principle (`2009-10` is a year, `34-79` a number); we resolve it by position (inside a value cell).
- The tehsil tables carry their column meaning only in multi-line Hindi headers.

**Implementation limits** (ours to fix):
- Only one district's abstract (Bhiwani) has been run; the other 19 Hindi PDFs are untested.
- 3,616 of the 4,820 Hindi rows have no unit (the column meaning is not parsed from the header), so they cannot pass a field-match check.
- Labels can have small decoding errors; numbers and units are not affected.
- PaddleOCR could not be tested here; the code path for it is written but unexecuted.
- Field accuracy against human-checked references is not yet measured (`eval/README.md`).
