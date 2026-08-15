import Foundation
import Vision
import AppKit

let path = CommandLine.arguments[1]
let url = URL(fileURLWithPath: path)
guard let image = NSImage(contentsOf: url) else {
    print("{\"status\":\"unavailable\",\"error\":\"image-open-failed\"}")
    exit(1)
}
var rect = NSRect(origin: .zero, size: image.size)
guard let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff), let cg = rep.cgImage else {
    print("{\"status\":\"unavailable\",\"error\":\"cgimage-failed\"}")
    exit(1)
}
let request = VNRecognizeTextRequest()
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do {
    try handler.perform([request])
    let observations = request.results ?? []
    let candidates = observations.compactMap { obs -> [String: Any]? in
        guard let top = obs.topCandidates(1).first else { return nil }
        return ["text": top.string, "confidence": top.confidence, "bbox": ["x": obs.boundingBox.origin.x, "y": obs.boundingBox.origin.y, "width": obs.boundingBox.size.width, "height": obs.boundingBox.size.height]]
    }
    let payload: [String: Any] = ["status": "complete", "engine": "Apple Vision VNRecognizeTextRequest", "os": ProcessInfo.processInfo.operatingSystemVersionString, "languages": ["zh-Hans", "en-US"], "candidate_count": candidates.count, "candidates": candidates]
    let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
    print(String(data: data, encoding: .utf8)!)
} catch {
    print("{\"status\":\"unavailable\",\"error\":\"ocr-failed\",\"detail\":\"\(error.localizedDescription)\"}")
    exit(1)
}
