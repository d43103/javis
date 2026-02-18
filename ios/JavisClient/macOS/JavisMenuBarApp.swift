import SwiftUI
import Combine

// MARK: - AppState

@MainActor
class MacAppState: ObservableObject {
    @Published var inputGain: Float = 1.0
    @Published var outputGain: Float = 1.0

    let hub: HubConnection
    let audio: AudioEngineMac

    private let sessionID = "voice-mac"

    init(hubURL: URL = URL(string: "ws://127.0.0.1:8766")!) {
        hub = HubConnection(hubURL: hubURL)
        audio = AudioEngineMac()

        audio.onPCMChunk = { [weak self] data in
            guard let self else { return }
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
            case .thinking:         return "ellipsis.circle"
            case .speaking:         return "speaker.wave.2"
            case .connected, .idle: return "mic.fill"
            default:                return "mic"
            }
        }()
        Image(systemName: img)
    }
}
