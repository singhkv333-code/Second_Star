# Spreadsheet widget — vendored libraries

Loaded on first open of a Sheet widget (js/w-sheet.js), never with the page.

| File | Package | Version | License |
|---|---|---|---|
| jspreadsheet.js, jspreadsheet.css | jspreadsheet-ce | 5.0.4 | MIT — https://github.com/jspreadsheet/ce |
| formula.js | @jspreadsheet/formula | 2.0.2 | MIT |
| jsuites.js, jsuites.css | jsuites | 5.12.0 | MIT — https://github.com/jsuites/jsuites |
| xlsx.full.min.js | SheetJS Community Edition | 0.20.3 | Apache-2.0 — https://cdn.sheetjs.com |

The npm-registry `xlsx` package is frozen at 0.18.5 with known advisories;
0.20.3 is from SheetJS's own CDN, as their docs direct.
