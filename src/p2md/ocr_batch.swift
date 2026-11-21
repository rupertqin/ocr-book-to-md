#!/usr/bin/env swift
// =============================================================
// 批量 OCR（macOS Vision 框架）
// 输入：一目录的 p{3..692}.png 扫描页
// 输出：每页两个文件
//   <txtDir>/<n>.txt   —— 纯文本（每行一条识别结果，从上到下）
//   <jsonDir>/<n>.json —— {"page":N,"lines":[{text,x,y,w,h},...]}
//                          x/y/w/h 为归一化坐标（0~1），原点在左下角
// 用法: swift ocr_batch.swift <imgDir> <txtDir> <jsonDir> [startPage] [endPage]
//
// 内存安全（对抗 SIGBUS/OOM，用户机器内存常被其他程序占满）:
//   1) 并发 1、每批 20 张，批间 group.wait() 同步 —— 峰值内存最低。
//   2) 每页处理前查可用内存（free+inactive），< 800MB 则 sleep 等系统回收
//      （inactive 页可回收），最多等 60s —— 渐进式内存耗尽的直接对策。
//   3) 图片先 Data(contentsOf:) 整读进内存再解码，且 kCGImageSourceShouldCache=false
//      —— 避免 CGImageSourceCreateWithURL 对外置卷文件 mmap（外置卷+低内存必 SIGBUS）。
//   4) 每页在 autoreleasepool 内解码+识别，处理完立即释放 CGImage/Vision buffer。
//   5) 进度输出逐行 fflush（管道默认块缓冲，用户看不到进度会以为卡死）。
//   6) <jsonDir>/<n>.json 已存在则跳过该页（续跑：崩溃后重跑从断点继续）。
// =============================================================
import Foundation
import Vision
import ImageIO
import CoreGraphics
import Darwin

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

let CONCURRENCY = 1     // 1 最稳（Vision 单页解码峰值可到数百 MB）
let BATCH = 20          // 每批 20，入队峰值低
let MEM_MIN_MB = 800.0  // 可用内存下限，低于则等待回收
let queue = OperationQueue()
queue.maxConcurrentOperationCount = CONCURRENCY
let sem = DispatchSemaphore(value: CONCURRENCY)
let group = DispatchGroup()
let lock = NSLock()
var failed: [Int] = []
var skipped = 0

func log(_ s: String) {
    print(s)
    fflush(stdout)
}

// 可用内存（free + inactive，单位 MB）；inactive 页可被系统随时回收
func availableMemoryMB() -> Double {
    var stats = vm_statistics64_data_t()
    var count = mach_msg_type_number_t(
        MemoryLayout<vm_statistics64_data_t>.size / MemoryLayout<integer_t>.size)
    let kr = withUnsafeMutablePointer(to: &stats) { p in
        p.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
            host_statistics64(mach_host_self(), HOST_VM_INFO64, $0, &count)
        }
    }
    guard kr == KERN_SUCCESS else { return 0 }
    let page = Double(vm_kernel_page_size)
    return (Double(stats.free_count) + Double(stats.inactive_count)) * page / (1024.0 * 1024.0)
}

// 内存不足时等待系统回收（最多 timeoutSec 秒，等不到也继续跑，不卡死）
func waitForMemory(_ minMB: Double, timeoutSec: Int = 60) {
    var waited = 0
    while availableMemoryMB() < minMB && waited < timeoutSec {
        if waited % 5 == 0 {
            log("  [mem] 可用 \(Int(availableMemoryMB()))MB < \(Int(minMB))MB，等待系统回收...")
        }
        Thread.sleep(forTimeInterval: 1.0)
        waited += 1
    }
}

func ocrPage(_ n: Int) {
    group.enter()
    queue.addOperation {
        defer { sem.signal(); group.leave() }
        sem.wait()

        // 续跑：输出已存在则跳过
        if FileManager.default.fileExists(atPath: "\(jsonDir)/\(n).json") {
            lock.lock(); skipped += 1; lock.unlock()
            return
        }

        waitForMemory(MEM_MIN_MB)

        autoreleasepool {
            let imgURL = URL(fileURLWithPath: "\(imgDir)/p\(n).jpg")
            // 先整读进内存再解码，避开外置卷 mmap；ShouldCache=false 减少解码缓存
            let opts = [kCGImageSourceShouldCache: false] as CFDictionary
            guard let data = try? Data(contentsOf: imgURL),
                  let src = CGImageSourceCreateWithData(data as CFData, opts),
                  let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
                log("  [skip] p\(n): 无法读取图片")
                lock.lock(); failed.append(n); lock.unlock()
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
                log("  [err] p\(n): \(error)")
                lock.lock(); failed.append(n); lock.unlock()
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
            log("  p\(n): \(obs.count) 行")
        }
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
log("完成。跳过(已存在): \(skipped) 失败页: \(failed)")
