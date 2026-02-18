import Foundation
import Combine

class HubConnection: NSObject, ObservableObject, URLSessionWebSocketDelegate {
    // MARK: - Published State
    @Published var status: SessionStatus = .disconnected
    @Published var partialText: String = ""
    @Published var conversation: [ConvMessage] = []

    // MARK: - Audio Callbacks
    /// Hub → client: called when TTS PCM float32 binary arrives
    var onAudioData: ((Data) -> Void)?
    /// client → Hub: send PCM int16 binary (called by AudioEngine)
    func sendAudio(_ data: Data) { sendBinary(data) }

    // MARK: - Private
    let hubURL: URL
    private var ws: URLSessionWebSocketTask?
    private var urlSession: URLSession?

    init(hubURL: URL) {
        self.hubURL = hubURL
        super.init()
    }

    // MARK: - Connection
    func connect(sessionID: String) {
        var comps = URLComponents(url: hubURL, resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "session_id", value: sessionID)]
        urlSession = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        ws = urlSession?.webSocketTask(with: comps.url!)
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
            if let t = msg.text, !t.isEmpty { append(.user, text: t) }
        case "ai":
            if let t = msg.text, !t.isEmpty { append(.ai, text: t) }
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
