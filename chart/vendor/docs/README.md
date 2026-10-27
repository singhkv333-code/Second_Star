# Documents widget — vendored

| File | Package | Version | License |
|---|---|---|---|
| mammoth.browser.min.js | mammoth (Word .docx to HTML) | 1.13.0 | BSD-2-Clause — https://github.com/mwilliamson/mammoth.js |
| pdf.min.js, pdf.worker.min.js | pdfjs-dist (PDF rendering) | 4.10.38 | Apache-2.0 — https://github.com/mozilla/pdf.js |

Each is loaded only when a file of its kind is opened in the Documents widget (js/w-docs.js).

## PDF.js 4.10.38 — Apache-2.0

`pdf.min.mjs` and `pdf.worker.min.mjs` from the npm package `pdfjs-dist@4.10.38`, renamed to `.js`: the VM's nginx serves `.mjs` as application/octet-stream, and a browser refuses to run a module of that type
(https://github.com/mozilla/pdf.js), unmodified; the licence is `LICENSE.pdfjs`.
The Documents widget draws PDFs with it instead of handing a blob URL to the
browser's own viewer, which Chrome blocks inside a frame when its "download
PDFs" setting or an extension says so, and which Android does not have.
