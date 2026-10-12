// vc-systemaudio: streams what the Mac plays (all apps) to stdout for VoiceCleaner.
//
// macOS has no loopback device; since macOS 14.2 Core Audio "process taps" capture system
// audio with the user's permission ("System Audio Recording"). This helper creates a
// stereo tap of all processes except the ones given with --exclude-pid (VoiceCleaner itself,
// so its own playback is not recorded), puts it in a private aggregate device, converts the
// audio to 48 kHz stereo Float32 and writes interleaved frames to stdout.
//
//   vc-systemaudio [--rate 48000] [--exclude-pid PID ...]
//
// stderr, one line each: {"ready": true, "rate": 48000, "channels": 2, "tap_rate": ...} once
// recording, or {"error": "..."} on failure (exit status 1). It stops and removes the tap and
// aggregate device when stdin closes (the parent quit or crashed) or on SIGTERM/SIGINT.
//
// Built by tools/build_mac.py into VoiceCleaner.app/Contents/MacOS (deployment target 14.2).

import AVFoundation
import CoreAudio
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(("{\"error\": \"" + message.replacingOccurrences(of: "\"", with: "'") + "\"}\n").data(using: .utf8)!)
    exit(1)
}

func say(_ json: String) {
    FileHandle.standardError.write((json + "\n").data(using: .utf8)!)
}

// --- arguments ----------------------------------------------------------------------------
var outRate: Double = 48_000
var excludePIDs: [pid_t] = []
var args = CommandLine.arguments.dropFirst().makeIterator()
while let a = args.next() {
    switch a {
    case "--rate": outRate = Double(args.next() ?? "") ?? outRate
    case "--exclude-pid": if let p = Int32(args.next() ?? "") { excludePIDs.append(p) }
    default: fail("unknown argument \(a)")
    }
}

// --- Core Audio helpers -------------------------------------------------------------------
func address(_ selector: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                               mElement: kAudioObjectPropertyElementMain)
}

func processObject(for pid: pid_t) -> AudioObjectID? {
    var addr = address(kAudioHardwarePropertyTranslatePIDToProcessObject)
    var pidVar = pid
    var obj = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    let st = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr,
                                        UInt32(MemoryLayout<pid_t>.size), &pidVar, &size, &obj)
    return st == noErr && obj != kAudioObjectUnknown ? obj : nil
}

func defaultOutputUID() -> String? {
    var addr = address(kAudioHardwarePropertyDefaultSystemOutputDevice)
    var device = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &device) == noErr
    else { return nil }
    var uidAddr = address(kAudioDevicePropertyDeviceUID)
    var uid: Unmanaged<CFString>?
    size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(device, &uidAddr, 0, nil, &size, &uid) == noErr, let u = uid else { return nil }
    return u.takeRetainedValue() as String
}

// --- tap + aggregate device ---------------------------------------------------------------
let excluded = excludePIDs.compactMap(processObject(for:))  // a PID that plays nothing yet has no object
let tapDesc = CATapDescription(stereoGlobalTapButExcludeProcesses: excluded)
tapDesc.name = "VoiceCleaner system audio"
tapDesc.uuid = UUID()
tapDesc.muteBehavior = .unmuted
tapDesc.isPrivate = true

var tapID = AudioObjectID(kAudioObjectUnknown)
var st = AudioHardwareCreateProcessTap(tapDesc, &tapID)
guard st == noErr else { fail("could not create the system audio tap (\(st))") }

var fmtAddr = address(kAudioTapPropertyFormat)
var tapASBD = AudioStreamBasicDescription()
var asbdSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
st = AudioObjectGetPropertyData(tapID, &fmtAddr, 0, nil, &asbdSize, &tapASBD)
guard st == noErr, let inFormat = AVAudioFormat(streamDescription: &tapASBD) else {
    AudioHardwareDestroyProcessTap(tapID)
    fail("could not read the tap format (\(st))")
}

guard let outputUID = defaultOutputUID() else {
    AudioHardwareDestroyProcessTap(tapID)
    fail("no output device")
}
let aggDesc: [String: Any] = [
    kAudioAggregateDeviceNameKey: "VoiceCleaner System Audio",
    kAudioAggregateDeviceUIDKey: UUID().uuidString,
    kAudioAggregateDeviceMainSubDeviceKey: outputUID,
    kAudioAggregateDeviceIsPrivateKey: true,
    kAudioAggregateDeviceIsStackedKey: false,
    kAudioAggregateDeviceTapAutoStartKey: true,
    kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID]],
    kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true,
                                       kAudioSubTapUIDKey: tapDesc.uuid.uuidString]],
]
var aggID = AudioObjectID(kAudioObjectUnknown)
st = AudioHardwareCreateAggregateDevice(aggDesc as CFDictionary, &aggID)
guard st == noErr else {
    AudioHardwareDestroyProcessTap(tapID)
    fail("could not create the capture device (\(st))")
}

// --- conversion to 48 kHz interleaved stereo Float32 --------------------------------------
guard let outFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: outRate, channels: 2, interleaved: true),
      let converter = AVAudioConverter(from: inFormat, to: outFormat) else {
    AudioHardwareDestroyAggregateDevice(aggID)
    AudioHardwareDestroyProcessTap(tapID)
    fail("unsupported tap format")
}
let stdout = FileHandle.standardOutput
let ioQueue = DispatchQueue(label: "vc-systemaudio.io")  // not the real-time thread: writing may block

var procID: AudioDeviceIOProcID?

// The tap delivers audio only while some (non-excluded) process plays: in silence the IOProc
// does not run at all. A recording must keep real time, so when no audio arrived for
// SILENCE_AFTER_S, digital silence is written up to the wall clock. Only ioQueue touches these.
let SILENCE_AFTER_S = 0.1
var startNanos: UInt64 = 0
var framesWritten: Int64 = 0
var lastAudioNanos: UInt64 = 0

func write(_ data: Data) {
    do { try stdout.write(contentsOf: data) } catch {
        DispatchQueue.main.async { stop(0) }  // the parent is gone
    }
}

func fillSilence() {
    let now = DispatchTime.now().uptimeNanoseconds
    guard startNanos > 0, Double(now - lastAudioNanos) / 1e9 >= SILENCE_AFTER_S else { return }
    let due = Int64(Double(now - startNanos) / 1e9 * outRate) - framesWritten
    guard due > 0 else { return }
    let frames = min(due, Int64(outRate))  // at most 1 s at a time
    write(Data(count: Int(frames) * 2 * MemoryLayout<Float>.size))
    framesWritten += frames
}

func stop(_ code: Int32) -> Never {
    if let proc = procID {
        AudioDeviceStop(aggID, proc)
        AudioDeviceDestroyIOProcID(aggID, proc)
    }
    AudioHardwareDestroyAggregateDevice(aggID)
    AudioHardwareDestroyProcessTap(tapID)
    exit(code)
}

st = AudioDeviceCreateIOProcIDWithBlock(&procID, aggID, ioQueue) { _, inInputData, _, _, _ in
    guard let inBuf = AVAudioPCMBuffer(pcmFormat: inFormat, bufferListNoCopy: inInputData, deallocator: nil),
          inBuf.frameLength > 0 else { return }
    let capacity = AVAudioFrameCount(Double(inBuf.frameLength) * outRate / inFormat.sampleRate) + 32
    guard let outBuf = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: capacity) else { return }
    var supplied = false
    var err: NSError?
    _ = converter.convert(to: outBuf, error: &err) { _, status in
        if supplied { status.pointee = .noDataNow; return nil }
        supplied = true
        status.pointee = .haveData
        return inBuf
    }
    guard err == nil, outBuf.frameLength > 0, let data = outBuf.floatChannelData else { return }
    lastAudioNanos = DispatchTime.now().uptimeNanoseconds
    write(Data(bytes: data[0], count: Int(outBuf.frameLength) * 2 * MemoryLayout<Float>.size))
    framesWritten += Int64(outBuf.frameLength)
}
guard st == noErr, let proc = procID else {
    AudioHardwareDestroyAggregateDevice(aggID)
    AudioHardwareDestroyProcessTap(tapID)
    fail("could not attach to the capture device (\(st))")
}

ioQueue.sync {
    startNanos = DispatchTime.now().uptimeNanoseconds
    lastAudioNanos = startNanos
}
st = AudioDeviceStart(aggID, proc)
guard st == noErr else { stop(1) }
let silenceTimer = DispatchSource.makeTimerSource(queue: ioQueue)
silenceTimer.schedule(deadline: .now() + .milliseconds(20), repeating: .milliseconds(20))
silenceTimer.setEventHandler { fillSilence() }
silenceTimer.resume()
say("{\"ready\": true, \"rate\": \(Int(outRate)), \"channels\": 2, \"tap_rate\": \(Int(inFormat.sampleRate))}")

// --- stop when the parent goes away -------------------------------------------------------
signal(SIGPIPE, SIG_IGN)
for sig in [SIGTERM, SIGINT] {
    signal(sig, SIG_IGN)
    let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    src.setEventHandler { stop(0) }
    src.resume()
    _ = Unmanaged.passRetained(src)  // keep the source alive
}
Thread.detachNewThread {
    while (try? FileHandle.standardInput.read(upToCount: 1)).map({ !($0?.isEmpty ?? true) }) ?? false {}
    DispatchQueue.main.async { stop(0) }  // stdin closed: the parent quit or crashed
}
dispatchMain()
