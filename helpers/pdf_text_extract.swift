import Foundation
import PDFKit

func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

guard CommandLine.arguments.count >= 2 else {
    fail("Usage: pdf_text_extract <pdf> [max-pages]")
}

let path = CommandLine.arguments[1]
let maxPages: Int
if CommandLine.arguments.count >= 3, let value = Int(CommandLine.arguments[2]), value > 0 {
    maxPages = value
} else {
    maxPages = 3
}

let url = URL(fileURLWithPath: path)
guard let document = PDFDocument(url: url) else {
    fail("Could not open PDF: \(path)", code: 2)
}

let count = min(document.pageCount, maxPages)
for index in 0..<count {
    if let page = document.page(at: index), let text = page.string, !text.isEmpty {
        print(text)
    }
}
