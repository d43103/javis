import Foundation
import Combine

/// Mac Hub WebSocket 연결 및 메시지 처리를 담당한다.
class HubConnection: NSObject, ObservableObject, URLSessionWebSocketDelegate {
    @Published var status: String = "disconnected"
    @Published var partialText: String = ""
    @Published var lastAI: String = ""

    private var ws: URLSessionWebSocketTask?
    private var session: URLSession?
    private let audioEngine = AudioEngine()

    var hubURL: URL

    init(hubURL: URL) {
        self.hubURL = hubURL
        super.init()
        audioEngine.onPCMChunk = { [weak self] data in
            self?.sendBinary(data)
        }
    }

    func connect(sessionID: String) {
        var comps = URLComponents(url: hubURL, resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "session_id", value: sessionID)]
        let url = comps.url!

        session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        ws = session?.webSocketTask(with: url)
        ws?.resume()
        receiveLoop()
        try? audioEngine.start()
        status = "connecting"
    }

    func disconnect() {
        ws?.cancel()
        audioEngine.stop()
        status = "disconnected"
    }

    private func receiveLoop() {
        ws?.receive { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let msg):
                self.handleMessage(msg)
                self.receiveLoop()
            case .failure:
                DispatchQueue.main.async { self.status = "disconnected" }
            }
        }
    }

    private func handleMessage(_ msg: URLSessionWebSocketTask.Message) {
        switch msg {
        case .data(let data):
            // TTS PCM float32 binary
            DispatchQueue.main.async {
                self.audioEngine.playPCMFloat32(data)
            }
        case .string(let text):
            guard let evt = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any]
            else { return }
            let type = evt["type"] as? String ?? ""
            DispatchQueue.main.async {
                switch type {
                case "status":
                    self.status = evt["value"] as? String ?? ""
                case "partial":
                    self.partialText = evt["text"] as? String ?? ""
                case "final":
                    self.partialText = ""
                case "ai":
                    self.lastAI = evt["text"] as? String ?? ""
                default: break
                }
            }
        @unknown default: break
        }
    }

    private func sendBinary(_ data: Data) {
        ws?.send(.data(data)) { _ in }
    }
}
