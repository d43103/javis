import SwiftUI

struct ContentView: View {
    @StateObject private var hub = HubConnection(
        hubURL: URL(string: "ws://192.168.219.106:8766")!
    )
    @State private var sessionID = "voice-mobile"

    var body: some View {
        VStack(spacing: 24) {
            Text("Javis")
                .font(.largeTitle.bold())

            StatusBadge(status: hub.status)

            if !hub.partialText.isEmpty {
                Text(hub.partialText)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }

            if !hub.lastAI.isEmpty {
                Text(hub.lastAI)
                    .font(.body)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }

            Spacer()

            Button(hub.status == "disconnected" ? "연결" : "연결 해제") {
                if hub.status == "disconnected" {
                    hub.connect(sessionID: sessionID)
                } else {
                    hub.disconnect()
                }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding()
    }
}

struct StatusBadge: View {
    let status: String
    var body: some View {
        HStack {
            Circle()
                .fill(color)
                .frame(width: 10, height: 10)
            Text(status)
                .font(.caption)
        }
    }
    var color: Color {
        switch status {
        case "connected", "idle": return .green
        case "thinking": return .yellow
        case "speaking": return .blue
        case "connecting": return .orange
        default: return .red
        }
    }
}
