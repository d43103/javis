import SwiftUI

@MainActor
class IOSAppState: ObservableObject {
    let hub = HubConnection(hubURL: URL(string: "ws://192.168.219.106:8766")!)
    let audio = AudioEngine()
    private let sessionID = "voice-mobile"

    init() {
        audio.onPCMChunk = { [weak self] data in
            self?.hub.sendAudio(data)
        }
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
        default:                return .gray
        }
    }
}
