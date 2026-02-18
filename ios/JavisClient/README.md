# JavisClient iOS

Javis 음성 비서 iOS 클라이언트.

## Xcode 프로젝트 생성 (수동)

1. Xcode → File → New → Project → iOS → App
2. Product Name: `JavisClient`
3. Bundle ID: `com.javis.client`
4. Language: Swift, Interface: SwiftUI
5. 저장 위치: 이 디렉토리 (`ios/JavisClient/`)

## Info.plist 설정

Background Audio 활성화:
```xml
<key>UIBackgroundModes</key>
<array>
    <string>audio</string>
</array>
```

마이크 권한:
```xml
<key>NSMicrophoneUsageDescription</key>
<string>음성 명령을 위해 마이크가 필요합니다.</string>
```

## 파일 추가

프로젝트 생성 후 다음 Swift 파일을 추가한다:
- `AudioEngine.swift` — AVAudioEngine 마이크 캡처 + TTS PCM 재생
- `HubConnection.swift` — URLSessionWebSocketTask 기반 Hub 연결
- `ContentView.swift` — SwiftUI 메인 화면

## Hub URL 설정

`ContentView.swift` 에서 Hub URL을 수정한다:
```swift
hubURL: URL(string: "ws://<MAC_IP>:8766")!
```
