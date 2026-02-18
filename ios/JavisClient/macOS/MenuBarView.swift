import SwiftUI

struct MenuBarView: View {
    @EnvironmentObject var state: MacAppState

    var body: some View {
        VStack(spacing: 0) {
            HeaderView(status: state.hub.status)
            Divider()
            AudioSectionView(
                icon: "🎤",
                label: "Input",
                gain: $state.inputGain,
                level: state.audio.inputLevel
            )
            Divider()
            AudioSectionView(
                icon: "🔊",
                label: "Output",
                gain: $state.outputGain,
                level: nil
            )
            Divider()
            ConversationView(messages: state.hub.conversation)
            Divider()
            FooterView(isRunning: state.isRunning) {
                if state.isRunning { state.stop() } else { state.start() }
            }
        }
        .frame(width: 280)
    }
}

// MARK: - Header

struct HeaderView: View {
    let status: SessionStatus

    var body: some View {
        HStack {
            Text("Javis")
                .font(.headline)
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
        case .thinking:         return .yellow
        case .speaking:         return .blue
        case .disconnected:     return .gray
        }
    }
}

// MARK: - Audio Section

struct AudioSectionView: View {
    let icon: String
    let label: String
    @Binding var gain: Float
    let level: Float?

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
            Slider(value: $gain, in: 0.0...2.0, step: 0.1)
                .tint(.accentColor)
            if let lvl = level {
                LevelMeterView(level: lvl)
                    .frame(height: 5)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }
}

// MARK: - VU Meter

struct LevelMeterView: View {
    var level: Float

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

// MARK: - Conversation

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
                            MessageRow(msg: msg).id(msg.id)
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

// MARK: - Footer

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
