#!/usr/bin/env swift
// =============================================================
// 批量 OCR（macOS Vision 框架）
// 输入：一目录的 p{3..692}.png 扫描页
// 输出：每页两个文件
//   <txtDir>/<n>.txt   —— 纯文本（每行一条识别结果，从上到下）
//   <jsonDir>/<n>.json —— {"page":N,"lines":[{text,x,y,w,h},...]}
//                          x/y/w/h 为归一化坐标（0~1），原点在左下角
// 用法: swift ocr_batch.swift <imgDir> <txtDir> <jsonDir> [startPage] [endPage]
// 注意: 大批量一次入队会 OOM（SIGKILL, exit 137）——本脚本每批 50 张、
//       并发 4（DispatchSemaphore），687 张约 13 分钟
// =============================================================
import Foundation
import Vision
import ImageIO
import CoreGraphics

let args = CommandLine.arguments
guard args.count >= 4 else {
    print("用法: ocr_batch.swift <imgDir> <txtDir> <jsonDir> [startPage] [endPage]")
    exit(1)
}
let imgDir  = args[1]
let txtDir  = args[2]
let jsonDir = args[3]
let startPage = args.count > 4 ? Int(args[4]) ?? 3 : 3
let endPage   = args.count > 5 ? Int(args[5]) ?? 692 : 692

try? FileManager.default.createDirectory(atPath: txtDir, withIntermediateDirectories: true)
try? FileManager.default.createDirectory(atPath: jsonDir, withIntermediateDirectories: true)

let CONCURRENCY = 4
let BATCH = 50
let queue = OperationQueue()
queue.maxConcurrentOperationCount = CONCURRENCY
let sem = DispatchSemaphore(value: CONCURRENCY)
let group = DispatchGroup()
var failed: [Int] = []

func ocrPage(_ n: Int) {
    group.enter()
    queue.addOperation {
        defer { sem.signal(); group.leave() }
        sem.wait()

        let imgURL = URL(fileURLWithPath: "\(imgDir)/p\(n).png")
        guard let src = CGImageSourceCreateWithURL(imgURL as CFURL, nil),
              let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
            print("  [skip] p\(n): 无法读取图片")
            return
        }

        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en"]   // 中文优先
        request.usesLanguageCorrection = true
        let handler = VNImageRequestHandler(cgImage: cg, options: [:])
        do {
            try handler.perform([request])
        } catch {
            print("  [err] p\(n): \(error)")
            failed.append(n)
            return
        }

        // Vision boundingBox: 归一化(0~1)、原点左下；按 minY 降序 = 从上到下
        let obs = (request.results ?? []).sorted { $0.boundingBox.minY > $1.boundingBox.minY }
        var lines: [[String: Any]] = []
        var text = ""
        for o in obs {
            guard let cand = o.topCandidates(1).first else { continue }
            let b = o.boundingBox
            lines.append([
                "text": cand.string,
                "x": Double(b.minX),
                "y": Double(b.minY),
                "w": Double(b.width),
                "h": Double(b.height),
            ])
            text += cand.string + "\n"
        }

        try? text.write(toFile: "\(txtDir)/\(n).txt", atomically: true, encoding: .utf8)
        let json: [String: Any] = ["page": n, "lines": lines]
        if let data = try? JSONSerialization.data(withJSONObject: json,
                                                  options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: URL(fileURLWithPath: "\(jsonDir)/\(n).json"))
        }
        print("  p\(n): \(obs.count) 行")
    }
}

// 每批 BATCH 张，批内并发 CONCURRENCY，批间同步（防止 OOM）
var batchStart = startPage
while batchStart <= endPage {
    let batchEnd = min(batchStart + BATCH - 1, endPage)
    for p in batchStart...batchEnd { ocrPage(p) }
    group.wait()
    batchStart = batchEnd + 1
}
print("完成。失败页: \(failed)")
