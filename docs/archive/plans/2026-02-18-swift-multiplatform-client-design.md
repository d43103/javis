# Design: Javis Swift 멀티플랫폼 클라이언트

**Date:** 2026-02-18
**Status:** Approved

## 목표

macOS 메뉴바 앱을 Python(rumps) → Swift로 전환한다. iOS 앱과 코드를 공유하고, 상용 소프트웨어 수준의 네이티브 macOS UI(NSPopover + SwiftUI)를 제공한다.

---

## 파일 구조

```
ios/JavisClient/
├── JavisClient.xcodeproj           ← 멀티타겟 Xcode 프로젝트
│
├── HubConnection.swift             ← [iOS + macOS 공유] WebSocket 클라이언트
├── Models.swift                    ← [iOS + macOS 공유] HubMessage, SessionStatus
│
├── iOS/
│   ├── AudioEngine.swift           ← iOS 오디오 (AVAudioSession)
│   └── ContentView.swift           ← iOS SwiftUI 메인 뷰
│
└── macOS/
    ├── JavisMenuBarApp.swift       ← @main, MenuBarExtra 진입점
    ├── AudioEngine+Mac.swift       ← macOS 오디오 (AVAudioEngine)
    └── MenuBarView.swift           ← SwiftUI Popover UI
```

기존 `src/menubar_app.py`와 `src/audio_devices.py`는 서버 컴포넌트만 남기고 클라이언트 부분 제거. Python Hub 서버(`src/voice_hub.py`)는 유지.

---

## 공유 컴포넌트

### `Models.swift`
```swift
enum SessionStatus: String {
    case idle, connected, thinking, speaking
}

struct HubMessage: Decodable {
    let type: String          // "status" | "partial" | "final" | "ai"
    let value: String?        // status용
    let text: String?         // partial/final/ai용
}
```

### `HubConnection.swift` (기존 파일 정리 + 공유)
- WebSocket URL: `ws://<host>:8766/ws/voice?session_id=<id>`
- 메시지 수신: status, partial, final, ai → `@Published` 프로퍼티
- PCM 송신: `sendAudio(_ data: Data)` 메서드
- 재연결 로직 포함

---

## macOS 메뉴바 앱

### 진입점 (`JavisMenuBarApp.swift`)
```swift
@main
struct JavisMenuBarApp: App {
    var body: some Scene {
        MenuBarExtra("J", systemImage: "mic.fill") {
            MenuBarView()
                .environmentObject(AppState())
        }
        .menuBarExtraStyle(.window)
    }
}
```
- `MenuBarExtra` (macOS 13+, `.window` style) → 팝오버 형태
- 상태에 따라 메뉴바 아이콘 변경: `mic.fill` / `waveform` / `speaker.wave.2`

### `AppState` (ObservableObject)
```swift
class AppState: ObservableObject {
    @Published var status: SessionStatus = .idle
    @Published var inputLevel: Float = 0.0     // VU 미터
    @Published var inputGain: Float = 1.0      // 0.0–2.0
    @Published var outputGain: Float = 1.0
    @Published var conversation: [ConvMessage] = []  // 최근 20개
    @Published var selectedInputDevice: String?
    @Published var selectedOutputDevice: String?
}
```

### UI 레이아웃 (`MenuBarView.swift`)

```
┌─────────────────────────────┐  폭: 280pt
│ ● Javis        ◉ listening  │  헤더 (상태 색상 변화)
├─────────────────────────────┤
│ 🎤  [MacBook Air Mic     ▼] │  Picker (AVAudioEngine devices)
│ Gain ●━━━━━━━━━━━━━━━  1.2x │  Slider (0.0~2.0)
│ ▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░  │  VU 미터 (10fps 업데이트)
├─────────────────────────────┤
│ 🔊  [MacBook Air Spkr   ▼] │
│ Gain ━━━━━━━━●━━━━━━━  1.6x │
├─────────────────────────────┤
│ 나: 파이썬이 뭐야?           │
│ 자: 파이썬은 프로그래밍      │  ScrollView (최근 10줄)
│    언어입니다. 1991년...     │  긴 줄 wrap, 자동 스크롤
│ 나: 만든 사람이 누구야?      │  색상: 나=회색, 자=파란색
│ 자: 귀도 반 로섬이           │
│    만들었어요.               │
├─────────────────────────────┤
│       [■ Stop]  [✕ Quit]   │
└─────────────────────────────┘
```

---

## macOS 오디오 (`AudioEngine+Mac.swift`)

- `AVAudioEngine` 사용 (iOS와 동일 프레임워크, API 일부 다름)
- **마이크 캡처**: `inputNode.installTap` → PCM → `HubConnection.sendAudio()`
- **VU 미터**: 탭 콜백에서 RMS 계산 → `AppState.inputLevel` 업데이트
- **TTS 재생**: `HubConnection`에서 받은 PCM → `AVAudioPlayerNode`

```swift
// RMS 계산
let rms = sqrt(buffer.floatChannelData![0][0..<frameCount].reduce(0) { $0 + $1*$1 } / Float(frameCount))
DispatchQueue.main.async { self.appState.inputLevel = min(rms * 5, 1.0) }
```

---

## VU 미터 뷰

```swift
struct LevelMeterView: View {
    var level: Float  // 0.0~1.0

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
        .frame(height: 6)
        .animation(.linear(duration: 0.05), value: level)
    }
}
```

---

## 제거 대상

- `src/menubar_app.py` — Swift로 대체
- `deploy/com.javis.menubar.plist` — Swift 앱은 Login Items 방식으로 자동 시작
- `scripts/install_client.sh` — 업데이트 필요

## 유지 대상

- `src/voice_hub.py` — Mac 서버 (Python, 변경 없음)
- `src/javis_stt/`, `src/javis_menubar.py` — 서버 컴포넌트
- `deploy/com.javis.hub.plist` — Hub 서버 launchd (변경 없음)

---

## 구현 순서

1. Xcode 프로젝트 생성 (iOS + macOS 멀티타겟)
2. `Models.swift` 작성 (공유)
3. `HubConnection.swift` 리팩터 (기존 코드 정리 + 공유 타겟 추가)
4. `AudioEngine+Mac.swift` 작성
5. `MenuBarView.swift` + `JavisMenuBarApp.swift` 작성
6. Python `menubar_app.py` 제거, launchd plist 제거
7. 통합 테스트

---

## 비고

- 최소 macOS 버전: 13.0 (Ventura) — `MenuBarExtra` 요구사항
- Xcode 프로젝트는 수동 생성 후 git 추가 (이전 iOS README와 동일 방식)
- Hub 서버 주소는 `hubURL` 설정으로 관리 (하드코딩 금지)
