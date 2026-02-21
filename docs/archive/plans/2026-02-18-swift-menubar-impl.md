# Swift Multiplatform Client Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** macOS 메뉴바 앱을 Python(rumps) → SwiftUI(MenuBarExtra + NSPopover)로 전환하고, iOS와 HubConnection.swift를 공유한다.

**Architecture:** `HubConnection`(공유)은 WebSocket만 담당, `AudioEngine`은 플랫폼별 분리. macOS는 `MenuBarExtra(.window)` + SwiftUI popover. VU 미터는 AVAudioEngine installTap에서 RMS 계산 후 `@Published` 업데이트.

**Tech Stack:** Swift 5.9, SwiftUI, AVFoundation, CoreAudio, URLSessionWebSocketTask, xcodegen

---

## 사전 읽기

- 기존 Swift 파일: `ios/JavisClient/JavisClient/{AudioEngine,HubConnection,ContentView}.swift`
- 설계 문서: `docs/plans/2026-02-18-swift-multiplatform-client-design.md`
- Python Hub 서버는 변경 없음: `src/voice_hub.py`

---

### Task 0: xcodegen 설치 및 파일 구조 정리

**Files:**
- Create: `ios/JavisClient/project.yml`
- Move: `ios/JavisClient/JavisClient/AudioEngine.swift` → `ios/JavisClient/iOS/AudioEngine.swift`
- Move: `ios/JavisClient/JavisClient/ContentView.swift` → `ios/JavisClient/iOS/ContentView.swift`
- Move: `ios/JavisClient/JavisClient/HubConnection.swift` → `ios/JavisClient/HubConnection.swift`
- Create: `ios/JavisClient/macOS/` (빈 디렉터리)

**Step 1: xcodegen 설치**

```bash
brew install xcodegen
```

Expected: `xcodegen version 2.x.x`

**Step 2: 디렉터리 생성 및 파일 이동**

```bash
cd /Users/d43103/Workspace/projects/javis/ios/JavisClient
mkdir -p iOS macOS
mv JavisClient/AudioEngine.swift iOS/
mv JavisClient/ContentView.swift iOS/
mv JavisClient/HubConnection.swift ./
# JavisClient/ 디렉터리는 이후 삭제
```

**Step 3: project.yml 작성**

```yaml
# ios/JavisClient/project.yml
name: JavisClient
options:
  minimumXcodeVersion: "15.0"
  defaultConfig: Debug

configs:
  Debug: debug
  Release: release

targets:
  JavisClient-iOS:
    type: application
    platform: iOS
    deploymentTarget: "16.0"
    sources:
      - path: HubConnection.swift
        createIntermediateGroups: true
      - path: Models.swift
        createIntermediateGroups: true
      - path: iOS
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: com.javis.client.ios
        INFOPLIST_KEY_NSMicrophoneUsageDescription: "Javis uses the microphone to capture your voice."
        INFOPLIST_KEY_UILaunchScreen_Generation: YES
        SWIFT_VERSION: 5.9
    scheme:
      testTargets:
        - JavisClientTests

  JavisClient-macOS:
    type: application
    platform: macOS
    deploymentTarget: "13.0"
    sources:
      - path: HubConnection.swift
        createIntermediateGroups: true
      - path: Models.swift
        createIntermediateGroups: true
      - path: macOS
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: com.javis.menubar
        INFOPLIST_KEY_NSMicrophoneUsageDescription: "Javis uses the microphone to capture your voice."
        INFOPLIST_KEY_LSUIElement: YES
        SWIFT_VERSION: 5.9
        CODE_SIGN_IDENTITY: "-"
    entitlements:
      path: macOS/JavisMenuBar.entitlements

  JavisClientTests:
    type: bundle.unit-test
    platform: iOS
    sources:
      - path: Tests
    dependencies:
      - target: JavisClient-iOS
```

**Step 4: Commit**

```bash
git add ios/JavisClient/
git commit -m "refactor: reorganize Swift sources for multiplatform"
```

---

### Task 1: Models.swift (공유)

**Files:**
- Create: `ios/JavisClient/Models.swift`
- Create: `ios/JavisClient/Tests/ModelsTests.swift`

**Step 1: Models.swift 작성**

```swift
// ios/JavisClient/Models.swift
import Foundation

enum SessionStatus: String, Equatable {
    case idle, connected, thinking, speaking, disconnected
}

struct HubMessage: Decodable {
    let type: String
    let value: String?   // "status" 메시지용
    let text: String?    // "partial"/"final"/"ai" 메시지용
}

struct ConvMessage: Identifiable, Equatable {
    enum Role { case user, ai }
    let id = UUID()
    let role: Role
    let text: String

    var prefix: String { role == .user ? "나" : "자" }
}
```

**Step 2: 실패 테스트 작성**

```swift
// ios/JavisClient/Tests/ModelsTests.swift
import XCTest
@testable import JavisClient_iOS

final class ModelsTests: XCTestCase {
    func test_decode_status_message() throws {
        let json = #"{"type":"status","value":"connected"}"#
        let msg = try JSONDecoder().decode(HubMessage.self, from: Data(json.utf8))
        XCTAssertEqual(msg.type, "status")
        XCTAssertEqual(msg.value, "connected")
        XCTAssertNil(msg.text)
    }

    func test_decode_ai_message() throws {
        let json = #"{"type":"ai","text":"안녕하세요."}"#
        let msg = try JSONDecoder().decode(HubMessage.self, from: Data(json.utf8))
        XCTAssertEqual(msg.type, "ai")
        XCTAssertEqual(msg.text, "안녕하세요.")
    }

    func test_convmessage_prefix() {
        let u = ConvMessage(role: .user, text: "hi")
        let a = ConvMessage(role: .ai, text: "hello")
        XCTAssertEqual(u.prefix, "나")
        XCTAssertEqual(a.prefix, "자")
    }
}
```

**Step 3: xcodegen 실행 후 Xcode에서 테스트**

```bash
cd ios/JavisClient && xcodegen generate
# Xcode: Cmd+U
```

Expected: 3 tests PASS

**Step 4: Commit**

```bash
git add ios/JavisClient/Models.swift ios/JavisClient/Tests/
git commit -m "feat: add Models.swift (SessionStatus, HubMessage, ConvMessage)"
```

---

### Task 2: HubConnection.swift 리팩터 (AudioEngine 분리)

**Files:**
- Modify: `ios/JavisClient/HubConnection.swift`
- Create: `ios/JavisClient/Tests/HubConnectionTests.swift`

**현재 문제:** `HubConnection`이 `AudioEngine`을 직접 생성 → 플랫폼별 다른 AudioEngine 사용 불가.

**목표:** `HubConnection`은 WebSocket + 메시지만 담당. 오디오는 콜백으로 위임.

**Step 1: 실패 테스트 작성**

```swift
// ios/JavisClient/Tests/HubConnectionTests.swift
import XCTest
@testable import JavisClient_iOS

final class HubConnectionTests: XCTestCase {
    func test_conversation_appends_final_and_ai() {
        let hub = HubConnection(hubURL: URL(string: "ws://localhost:8766")!)
        hub.handleIncomingText(#"{"type":"final","text":"파이썬이 뭐야?"}"#)
        hub.handleIncomingText(#"{"type":"ai","text":"파이썬은 프로그래밍 언어."}"#)
        XCTAssertEqual(hub.conversation.count, 2)
        XCTAssertEqual(hub.conversation[0].role, .user)
        XCTAssertEqual(hub.conversation[1].role, .ai)
    }

    func test_status_updates_on_status_message() {
        let hub = HubConnection(hubURL: URL(string: "ws://localhost:8766")!)
        hub.handleIncomingText(#"{"type":"status","value":"thinking"}"#)
        XCTAssertEqual(hub.status, .thinking)
    }

    func test_audio_callback_called_on_binary() {
        let hub = HubConnection(hubURL: URL(string: "ws://localhost:8766")!)
        var received: Data?
        hub.onAudioData = { received = $0 }
        hub.handleIncomingBinary(Data([0x01, 0x02]))
        XCTAssertEqual(received, Data([0x01, 0x02]))
    }
}
```

**Step 2: Xcode에서 테스트 실행 — FAIL 확인**

`handleIncomingText`, `handleIncomingBinary`, `onAudioData` 없음 → 컴파일 에러

**Step 3: HubConnection.swift 전면 교체**

```swift
// ios/JavisClient/HubConnection.swift
import Foundation
import Combine

class HubConnection: NSObject, ObservableObject, URLSessionWebSocketDelegate {
    // MARK: - Published State
    @Published var status: SessionStatus = .disconnected
    @Published var partialText: String = ""
    @Published var conversation: [ConvMessage] = []

    // MARK: - Callbacks (플랫폼별 AudioEngine이 세팅)
    /// Hub → 클라이언트: TTS PCM float32 binary 도착 시 호출
    var onAudioData: ((Data) -> Void)?
    /// 클라이언트 → Hub: PCM int16 binary 전송 함수 (AudioEngine이 호출)
    func sendAudio(_ data: Data) { sendBinary(data) }

    // MARK: - Private
    let hubURL: URL
    private var ws: URLSessionWebSocketTask?
    private var session: URLSession?

    init(hubURL: URL) {
        self.hubURL = hubURL
        super.init()
    }

    // MARK: - Connection
    func connect(sessionID: String) {
        var comps = URLComponents(url: hubURL, resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "session_id", value: sessionID)]
        session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        ws = session?.webSocketTask(with: comps.url!)
        ws?.resume()
        receiveLoop()
        DispatchQueue.main.async { self.status = .connected }
    }

    func disconnect() {
        ws?.cancel()
        ws = nil
        DispatchQueue.main.async { self.status = .disconnected }
    }

    // MARK: - Receiving
    private func receiveLoop() {
        ws?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let msg):
                switch msg {
                case .data(let d):   self.handleIncomingBinary(d)
                case .string(let s): self.handleIncomingText(s)
                @unknown default: break
                }
                self.receiveLoop()
            case .failure:
                DispatchQueue.main.async { self.status = .disconnected }
            }
        }
    }

    // internal visibility for tests
    func handleIncomingText(_ text: String) {
        guard let msg = try? JSONDecoder().decode(HubMessage.self, from: Data(text.utf8))
        else { return }
        DispatchQueue.main.async { self.apply(msg) }
    }

    func handleIncomingBinary(_ data: Data) {
        onAudioData?(data)
    }

    private func apply(_ msg: HubMessage) {
        switch msg.type {
        case "status":
            status = SessionStatus(rawValue: msg.value ?? "") ?? .idle
        case "partial":
            partialText = msg.text ?? ""
        case "final":
            partialText = ""
            if let t = msg.text, !t.isEmpty {
                append(.user, text: t)
            }
        case "ai":
            if let t = msg.text, !t.isEmpty {
                append(.ai, text: t)
            }
        default: break
        }
    }

    private func append(_ role: ConvMessage.Role, text: String) {
        conversation.append(ConvMessage(role: role, text: text))
        if conversation.count > 20 { conversation.removeFirst() }
    }

    // MARK: - Sending
    private func sendBinary(_ data: Data) {
        ws?.send(.data(data)) { _ in }
    }
}
```

**Step 4: 테스트 실행 — PASS 확인**

```bash
# Xcode: Cmd+U
```

Expected: 3 tests PASS

**Step 5: Commit**

```bash
git add ios/JavisClient/HubConnection.swift ios/JavisClient/Tests/HubConnectionTests.swift
git commit -m "refactor: decouple HubConnection from AudioEngine, add conversation history"
```

---

### Task 3: iOS ContentView 업데이트 (HubConnection 재배선)

**Files:**
- Modify: `ios/JavisClient/iOS/ContentView.swift`
- Modify: `ios/JavisClient/iOS/AudioEngine.swift` (onPCMChunk 확인)

HubConnection이 AudioEngine을 직접 안 만들기 때문에, iOS 앱에서 둘을 연결해야 한다.

**Step 1: iOS/ContentView.swift 업데이트**

```swift
// ios/JavisClient/iOS/ContentView.swift
import SwiftUI

@MainActor
class IOSAppState: ObservableObject {
    let hub = HubConnection(hubURL: URL(string: "ws://192.168.219.106:8766")!)
    let audio = AudioEngine()
    private let sessionID = "voice-mobile"

    init() {
        // AudioEngine → Hub: mic PCM 전송
        audio.onPCMChunk = { [weak self] data in
            self?.hub.sendAudio(data)
        }
        // Hub → AudioEngine: TTS 재생
        hub.onAudioData = { [weak self] data in
            self?.audio.playPCMFloat32(data)
        }
    }

    func connect() {
        try? audio.start()
        hub.connect(sessionID: sessionID)
    }

    func disconnect() {
        audio.stop()
        hub.disconnect()
    }
}

struct ContentView: View {
    @StateObject private var state = IOSAppState()

    var body: some View {
        VStack(spacing: 24) {
            Text("Javis").font(.largeTitle.bold())
            StatusBadge(status: state.hub.status)

            if !state.hub.partialText.isEmpty {
                Text(state.hub.partialText)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(state.hub.conversation) { msg in
                        HStack(alignment: .top, spacing: 6) {
                            Text(msg.prefix + ":")
                                .bold()
                                .foregroundColor(msg.role == .user ? .secondary : .accentColor)
                            Text(msg.text)
                        }
                        .font(.callout)
                        .padding(.horizontal)
                    }
                }
            }
            .frame(maxHeight: 200)

            Spacer()

            Button(state.hub.status == .disconnected ? "연결" : "연결 해제") {
                if state.hub.status == .disconnected {
                    state.connect()
                } else {
                    state.disconnect()
                }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding()
    }
}

struct StatusBadge: View {
    let status: SessionStatus
    var body: some View {
        HStack {
            Circle().fill(color).frame(width: 10, height: 10)
            Text(status.rawValue).font(.caption)
        }
    }
    var color: Color {
        switch status {
        case .connected, .idle: return .green
        case .thinking:         return .yellow
        case .speaking:         return .blue
        case .connected:        return .orange
        default:                return .red
        }
    }
}
```

**Step 2: iOS 빌드 확인**

Xcode → JavisClient-iOS 타겟 선택 → Cmd+B

Expected: Build Succeeded

**Step 3: Commit**

```bash
git add ios/JavisClient/iOS/ContentView.swift
git commit -m "feat: wire IOSAppState connecting AudioEngine <-> HubConnection"
```

---

### Task 4: macOS AudioEngine (`AudioEngine+Mac.swift`)

**Files:**
- Create: `ios/JavisClient/macOS/AudioEngine+Mac.swift`
- Create: `ios/JavisClient/Tests/AudioEngineMacTests.swift`

macOS는 `AVAudioSession` 없음. `AVAudioEngine`만 사용.

**Step 1: 실패 테스트 작성**

```swift
// ios/JavisClient/Tests/AudioEngineMacTests.swift
// macOS 전용 (iOS target에서는 skip)
#if os(macOS)
import XCTest
@testable import JavisClient_macOS

final class AudioEngineMacTests: XCTestCase {
    func test_inputLevel_starts_at_zero() {
        let engine = AudioEngineMac()
        XCTAssertEqual(engine.inputLevel, 0.0)
    }

    func test_playPCMFloat32_does_not_crash_with_empty_data() {
        let engine = AudioEngineMac()
        engine.playPCMFloat32(Data())  // must not crash
    }
}
#endif
```

**Step 2: AudioEngine+Mac.swift 작성**

```swift
// ios/JavisClient/macOS/AudioEngine+Mac.swift
import AVFoundation
import Combine

class AudioEngineMac: ObservableObject {
    @Published var inputLevel: Float = 0.0

    var onPCMChunk: ((Data) -> Void)?

    private let engine = AVAudioEngine()
    private let playerNode = AVAudioPlayerNode()
    private let MIC_RATE: Double = 16000
    private let TTS_RATE: Double = 24000
    private let CHUNK_FRAMES = 1280  // 80ms @ 16kHz

    init() {
        engine.attach(playerNode)
        let ttsFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: TTS_RATE, channels: 1, interleaved: false)!
        engine.connect(playerNode, to: engine.mainMixerNode, format: ttsFormat)
    }

    func start() throws {
        let micFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: MIC_RATE, channels: 1, interleaved: true)!

        engine.inputNode.installTap(
            onBus: 0,
            bufferSize: AVAudioFrameCount(CHUNK_FRAMES),
            format: micFormat
        ) { [weak self] buffer, _ in
            guard let self else { return }
            // PCM 전송
            if let data = self.bufferToData(buffer) {
                self.onPCMChunk?(data)
            }
            // VU 미터 RMS 계산
            let frames = Int(buffer.frameLength)
            if frames > 0, let ptr = buffer.int16ChannelData?[0] {
                let sum = (0..<frames).reduce(0.0) { acc, i in
                    let s = Float(ptr[i]) / 32768.0
                    return acc + s * s
                }
                let rms = min(sqrt(sum / Float(frames)) * 5.0, 1.0)
                DispatchQueue.main.async { self.inputLevel = rms }
            }
        }

        try engine.start()
        playerNode.play()
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        DispatchQueue.main.async { self.inputLevel = 0.0 }
    }

    func playPCMFloat32(_ data: Data) {
        let floatCount = data.count / 4
        guard floatCount > 0 else { return }
        let format = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: TTS_RATE, channels: 1, interleaved: false)!
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format,
                                            frameCapacity: AVAudioFrameCount(floatCount)) else { return }
        buffer.frameLength = AVAudioFrameCount(floatCount)
        data.withUnsafeBytes { ptr in
            buffer.floatChannelData?[0].update(
                from: ptr.bindMemory(to: Float.self).baseAddress!,
                count: floatCount)
        }
        playerNode.scheduleBuffer(buffer)
    }

    private func bufferToData(_ buffer: AVAudioPCMBuffer) -> Data? {
        guard let ch = buffer.int16ChannelData else { return nil }
        return Data(bytes: ch[0], count: Int(buffer.frameLength) * 2)
    }
}
```

**Step 3: 테스트 실행 (macOS 타겟)**

Xcode → JavisClient-macOS 선택 → Cmd+U

Expected: 2 tests PASS

**Step 4: Commit**

```bash
git add ios/JavisClient/macOS/AudioEngine+Mac.swift ios/JavisClient/Tests/AudioEngineMacTests.swift
git commit -m "feat: add macOS AudioEngine with VU meter (AVAudioEngine, no AVAudioSession)"
```

---

### Task 5: macOS entitlements 파일

**Files:**
- Create: `ios/JavisClient/macOS/JavisMenuBar.entitlements`

macOS 마이크 권한에 entitlements 필요.

**Step 1: entitlements 파일 작성**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.device.audio-input</key>
    <true/>
    <key>com.apple.security.network.client</key>
    <true/>
</dict>
</plist>
```

**Step 2: Commit**

```bash
git add ios/JavisClient/macOS/JavisMenuBar.entitlements
git commit -m "feat: add macOS entitlements (microphone, network)"
```

---

### Task 6: macOS UI (LevelMeterView + MenuBarView)

**Files:**
- Create: `ios/JavisClient/macOS/MenuBarView.swift`
- Create: `ios/JavisClient/macOS/JavisMenuBarApp.swift`

**Step 1: JavisMenuBarApp.swift 작성 (진입점 + AppState)**

```swift
// ios/JavisClient/macOS/JavisMenuBarApp.swift
import SwiftUI
import Combine

// MARK: - AppState

@MainActor
class MacAppState: ObservableObject {
    @Published var inputGain: Float = 1.0 {
        didSet { /* gain은 audioEngine에서 PCM 전송 시 적용 — Task 7에서 연결 */ }
    }
    @Published var outputGain: Float = 1.0

    let hub: HubConnection
    let audio: AudioEngineMac

    private let sessionID = "voice-mac"

    init(hubURL: URL = URL(string: "ws://127.0.0.1:8766")!) {
        hub = HubConnection(hubURL: hubURL)
        audio = AudioEngineMac()

        audio.onPCMChunk = { [weak self] data in
            guard let self else { return }
            // gain 적용 (int16 PCM 스케일)
            let scaled = Self.applyGainInt16(data, gain: self.inputGain)
            self.hub.sendAudio(scaled)
        }
        hub.onAudioData = { [weak self] data in
            guard let self else { return }
            let scaled = Self.applyGainFloat32(data, gain: self.outputGain)
            self.audio.playPCMFloat32(scaled)
        }
    }

    func start() {
        try? audio.start()
        hub.connect(sessionID: sessionID)
    }

    func stop() {
        audio.stop()
        hub.disconnect()
    }

    var isRunning: Bool { hub.status != .disconnected }

    // PCM gain helpers
    static func applyGainInt16(_ data: Data, gain: Float) -> Data {
        guard gain != 1.0 else { return data }
        var result = data
        result.withUnsafeMutableBytes { ptr in
            let buf = ptr.bindMemory(to: Int16.self)
            for i in buf.indices {
                let v = Float(buf[i]) * gain
                buf[i] = Int16(max(-32768, min(32767, v)))
            }
        }
        return result
    }

    static func applyGainFloat32(_ data: Data, gain: Float) -> Data {
        guard gain != 1.0 else { return data }
        var result = data
        result.withUnsafeMutableBytes { ptr in
            let buf = ptr.bindMemory(to: Float.self)
            for i in buf.indices { buf[i] *= gain }
        }
        return result
    }
}

// MARK: - App Entry Point

@main
struct JavisMenuBarApp: App {
    @StateObject private var state = MacAppState()

    var body: some Scene {
        MenuBarExtra {
            MenuBarView()
                .environmentObject(state)
        } label: {
            MenuBarIcon(status: state.hub.status)
        }
        .menuBarExtraStyle(.window)
    }
}

struct MenuBarIcon: View {
    let status: SessionStatus
    var body: some View {
        let img: String = {
            switch status {
            case .thinking:  return "ellipsis.circle"
            case .speaking:  return "speaker.wave.2"
            case .connected, .idle: return "mic.fill"
            default:         return "mic"
            }
        }()
        Image(systemName: img)
    }
}
```

**Step 2: MenuBarView.swift 작성**

```swift
// ios/JavisClient/macOS/MenuBarView.swift
import SwiftUI

struct MenuBarView: View {
    @EnvironmentObject var state: MacAppState
    @Environment(\.openURL) var openURL

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HeaderView(status: state.hub.status)

            Divider()

            // Input section
            AudioSectionView(
                icon: "🎤",
                label: "Input",
                gain: $state.inputGain,
                level: state.audio.inputLevel
            )

            Divider()

            // Output section
            AudioSectionView(
                icon: "🔊",
                label: "Output",
                gain: $state.outputGain,
                level: nil  // 출력 VU 미터는 추후
            )

            Divider()

            // Conversation
            ConversationView(messages: state.hub.conversation)

            Divider()

            // Footer buttons
            FooterView(isRunning: state.isRunning) {
                if state.isRunning { state.stop() } else { state.start() }
            }
        }
        .frame(width: 280)
    }
}

// MARK: - Sub Views

struct HeaderView: View {
    let status: SessionStatus

    var body: some View {
        HStack {
            Text("Javis")
                .font(.headline)
                .foregroundColor(.primary)
            Spacer()
            HStack(spacing: 5) {
                Circle()
                    .fill(statusColor)
                    .frame(width: 8, height: 8)
                Text(statusLabel)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
    }

    var statusLabel: String {
        switch status {
        case .idle:         return "ready"
        case .connected:    return "listening"
        case .thinking:     return "thinking…"
        case .speaking:     return "speaking"
        case .disconnected: return "stopped"
        }
    }

    var statusColor: Color {
        switch status {
        case .connected, .idle: return .green
        case .thinking:          return .yellow
        case .speaking:          return .blue
        case .disconnected:      return .gray
        }
    }
}

struct AudioSectionView: View {
    let icon: String
    let label: String
    @Binding var gain: Float
    let level: Float?  // nil이면 VU미터 숨김

    var body: some View {
        VStack(spacing: 4) {
            HStack {
                Text("\(icon)  \(label)")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                Spacer()
                Text(String(format: "%.1fx", gain))
                    .font(.caption.monospacedDigit())
                    .foregroundColor(.secondary)
            }

            // Gain Slider
            Slider(value: $gain, in: 0.0...2.0, step: 0.1)
                .tint(.accentColor)

            // VU Meter (입력에만 표시)
            if let lvl = level {
                LevelMeterView(level: lvl)
                    .frame(height: 5)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }
}

struct LevelMeterView: View {
    var level: Float  // 0.0 ~ 1.0

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 2)
                    .fill(Color.secondary.opacity(0.2))
                RoundedRectangle(cornerRadius: 2)
                    .fill(level > 0.8 ? Color.red : Color.accentColor)
                    .frame(width: geo.size.width * CGFloat(level))
            }
        }
        .animation(.linear(duration: 0.05), value: level)
    }
}

struct ConversationView: View {
    let messages: [ConvMessage]

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 6) {
                    if messages.isEmpty {
                        Text("대화를 시작하세요…")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .frame(maxWidth: .infinity, alignment: .center)
                            .padding(.top, 8)
                    } else {
                        ForEach(messages) { msg in
                            MessageRow(msg: msg)
                                .id(msg.id)
                        }
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
            }
            .frame(height: 160)
            .onChange(of: messages.count) { _ in
                if let last = messages.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }
}

struct MessageRow: View {
    let msg: ConvMessage

    var body: some View {
        HStack(alignment: .top, spacing: 4) {
            Text(msg.prefix + ":")
                .bold()
                .font(.caption)
                .foregroundColor(msg.role == .user ? .secondary : .accentColor)
                .frame(width: 18, alignment: .leading)
            Text(msg.text)
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct FooterView: View {
    let isRunning: Bool
    let toggle: () -> Void

    var body: some View {
        HStack {
            Button(action: toggle) {
                Label(isRunning ? "Stop" : "Start",
                      systemImage: isRunning ? "stop.fill" : "play.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(isRunning ? .red : .accentColor)

            Button("Quit") { NSApplication.shared.terminate(nil) }
                .buttonStyle(.bordered)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
    }
}
```

**Step 3: macOS 타겟 빌드 확인**

Xcode → JavisClient-macOS → Cmd+B

Expected: Build Succeeded (경고는 무시, 에러는 수정)

**Step 4: 앱 실행 테스트**

Xcode → Cmd+R → 메뉴바에 🎤 아이콘 확인 → 클릭 → Popover 확인

체크리스트:
- [ ] 헤더에 "Javis" + 상태 표시
- [ ] Input/Output Gain 슬라이더 작동
- [ ] VU 미터 표시됨 (Start 후 말하면 움직임)
- [ ] Start/Stop 버튼 작동
- [ ] 대화 후 나:/자: 메시지 표시

**Step 5: Commit**

```bash
git add ios/JavisClient/macOS/
git commit -m "feat: add macOS MenuBarView SwiftUI popover with VU meter and gain sliders"
```

---

### Task 7: xcodegen project.yml 최종 확정 및 생성

**Files:**
- Modify: `ios/JavisClient/project.yml` (빌드 설정 보완)

**Step 1: project.yml 최종본 확인 및 `xcodegen generate`**

```bash
cd ios/JavisClient
xcodegen generate --spec project.yml
```

**Step 2: .gitignore에 Xcode 파생 파일 추가**

`ios/JavisClient/.gitignore`:
```
*.xcworkspace/xcuserdata/
*.xcodeproj/xcuserdata/
DerivedData/
.build/
```

**Step 3: xcodeproj를 git에 커밋**

```bash
git add ios/JavisClient/JavisClient.xcodeproj ios/JavisClient/.gitignore
git commit -m "feat: add xcodegen project.yml and generated xcodeproj"
```

---

### Task 8: Python 메뉴바 앱 제거 및 정리

**Files:**
- Delete: `src/menubar_app.py`
- Delete: `deploy/com.javis.voice-bridge.plist` (이미 삭제됨)
- Modify: `scripts/install_client.sh`
- Modify: `docs/roadmap.md`

**Step 1: Python 메뉴바 서비스 중지**

```bash
launchctl bootout gui/$(id -u)/com.javis.menubar 2>/dev/null || true
```

**Step 2: 파일 삭제**

```bash
git rm src/menubar_app.py
```

주의: `src/audio_devices.py`는 **삭제하지 않음** — `voice_hub.py`가 여전히 사용함.

**Step 3: install_client.sh 업데이트**

`scripts/install_client.sh`에서 Python 메뉴바 관련 항목 제거. Swift 앱 설치 안내 추가:

```bash
# Swift 메뉴바 앱 빌드 및 설치
echo "Swift 메뉴바 앱 빌드:"
echo "  1. Xcode에서 ios/JavisClient/JavisClient.xcodeproj 열기"
echo "  2. JavisClient-macOS 타겟 선택"
echo "  3. Cmd+B 로 빌드"
echo "  4. 빌드된 .app을 ~/Applications/ 에 복사"
echo "  5. 시스템 설정 > 일반 > 로그인 항목에 추가"
```

**Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove Python menubar app, update install script for Swift client"
```

---

## 검증 체크리스트

빌드 완료 후 실제 Hub 서버와 함께 테스트:

1. Hub 서버 실행 확인: `launchctl list | grep com.javis.hub`
2. Swift 앱 실행: Xcode Cmd+R
3. Start 버튼 클릭 → 메뉴바 아이콘 🎤→ 발화
4. VU 미터가 말할 때 올라가는지 확인
5. 상태가 "listening" → "thinking…" → "speaking" → "ready"로 전환되는지
6. 대화 내역이 나:/자: 형태로 쌓이는지
7. Gain 슬라이더 조정 후 재발화 — 볼륨 변화 확인

```bash
# Hub 에러 로그 확인
tail -f /tmp/javis-hub-error.log
```
