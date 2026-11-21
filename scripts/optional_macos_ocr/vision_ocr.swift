import Foundation
import Vision
import ImageIO
import CoreGraphics

struct CharBox: Codable {
    let char: String
    let index: Int
    let bbox: [Double] // [x0, y0, x1, y1] in top-left pixel coordinates
}

struct LineObs: Codable {
    let line_index: Int
    let text: String
    let confidence: Float
    let bbox: [Double] // [x0, y0, x1, y1] in top-left pixel coordinates
    let chars: [CharBox]
}

struct ImageOCRResult: Codable {
    let image: String
    let width: Int
    let height: Int
    let lines: [LineObs]
}

func convertRect(_ r: CGRect, width: Int, height: Int) -> [Double] {
    let w = Double(width)
    let h = Double(height)
    let x0 = r.minX * w
    let x1 = r.maxX * w
    // Vision normalized coords have origin at bottom-left; convert to top-left pixel coords
    let y0 = (1.0 - r.maxY) * h
    let y1 = (1.0 - r.minY) * h
    return [x0, y0, x1, y1]
}

func runOCR(on url: URL) throws -> ImageOCRResult {
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let cgImg = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        throw NSError(domain: "VisionOCR", code: 1, userInfo: [NSLocalizedDescriptionKey: "Failed to load \(url.path)"])
    }
    let width = cgImg.width
    let height = cgImg.height

    var lines: [LineObs] = []
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = true
    request.minimumTextHeight = 0.008

    let handler = VNImageRequestHandler(cgImage: cgImg, options: [:])
    try handler.perform([request])

    let observations = (request.results as? [VNRecognizedTextObservation]) ?? []
    // Sort top-to-bottom by top-left y0
    let sortedObs = observations.sorted { (a, b) -> Bool in
        let ay = 1.0 - a.boundingBox.maxY
        let by = 1.0 - b.boundingBox.maxY
        if abs(ay - by) > 0.01 { return ay < by }
        return a.boundingBox.minX < b.boundingBox.minX
    }

    for (idx, obs) in sortedObs.enumerated() {
        guard let top = obs.topCandidates(1).first else { continue }
        let str = top.string
        let lineBox = convertRect(obs.boundingBox, width: width, height: height)
        var charBoxes: [CharBox] = []
        var cur = str.startIndex
        var charIdx = 0
        while cur < str.endIndex {
            let nxt = str.index(after: cur)
            let chStr = String(str[cur..<nxt])
            if let rectObs = try? top.boundingBox(for: cur..<nxt) {
                let cbox = convertRect(rectObs.boundingBox, width: width, height: height)
                charBoxes.append(CharBox(char: chStr, index: charIdx, bbox: cbox))
            }
            cur = nxt
            charIdx += 1
        }
        lines.append(LineObs(
            line_index: idx,
            text: str,
            confidence: top.confidence,
            bbox: lineBox,
            chars: charBoxes
        ))
    }

    let name = url.lastPathComponent.replacingOccurrences(of: "_clean.png", with: "")
    return ImageOCRResult(image: name, width: width, height: height, lines: lines)
}

let args = CommandLine.arguments
guard args.count >= 3 else {
    fputs("Usage: vision_ocr <preprocessed_dir> <output_json>\n", stderr)
    exit(1)
}

let inputDir = URL(fileURLWithPath: args[1])
let outputURL = URL(fileURLWithPath: args[2])

let fm = FileManager.default
let files = try fm.contentsOfDirectory(at: inputDir, includingPropertiesForKeys: nil)
    .filter { $0.lastPathComponent.hasSuffix("_clean.png") }
    .sorted { $0.lastPathComponent < $1.lastPathComponent }

var results: [ImageOCRResult] = []
for f in files {
    let res = try runOCR(on: f)
    results.append(res)
    let totalChars = res.lines.reduce(0) { $0 + $1.chars.count }
    print("[OCR] \(res.image): \(res.lines.count) lines, \(totalChars) char boxes")
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
let data = try encoder.encode(results)
try data.write(to: outputURL)
print("[DONE] Saved OCR results -> \(outputURL.path)")
