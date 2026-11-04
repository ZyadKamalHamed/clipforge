// ClipForge's notifier (SPEC 18), built into ClipForge.app by scripts/make_app.sh. ClipForge starts it with
// --title, --body and --open; it shows the notification and, when the notification is clicked, opens the page
// in a ClipForge window as clipforge/launcher.py does. Only ClipForge's own pages on this Mac are opened, the
// same pattern clipforge/notify.py checks before it asks.

import AppKit
import UserNotifications

let page = try! NSRegularExpression(pattern: #"\Ahttp://127\.0\.0\.1:[0-9]{2,5}/[A-Za-z0-9/_-]*\z"#)

func argument(_ flag: String) -> String? {
    let args = CommandLine.arguments
    guard let at = args.firstIndex(of: flag), at + 1 < args.count else { return nil }
    return args[at + 1]
}

func isPage(_ url: String) -> Bool {
    page.firstMatch(in: url, range: NSRange(url.startIndex..., in: url)) != nil
}

final class Notifier: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        // A click on an older notification starts the helper with no arguments: it only waits to hear which.
        if let title = argument("--title"), let body = argument("--body") {
            let url = argument("--open").flatMap { isPage($0) ? $0 : nil }
            center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
                guard granted else { return }
                let content = UNMutableNotificationContent()
                content.title = title
                content.body = body
                if let url {
                    content.userInfo = ["url": url]
                }
                center.add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
            }
        }
        // Long enough to show it, or to hear a click, then gone.
        DispatchQueue.main.asyncAfter(deadline: .now() + 30) { NSApp.terminate(nil) }
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .list])
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        if response.actionIdentifier == UNNotificationDefaultActionIdentifier,
            let url = response.notification.request.content.userInfo["url"] as? String, isPage(url)
        {
            let open = Process()
            open.executableURL = URL(fileURLWithPath: "/usr/bin/open")
            open.arguments = ["-na", "Google Chrome", "--args", "--app=\(url)"]
            try? open.run()
        }
        completionHandler()
    }
}

let app = NSApplication.shared
let notifier = Notifier()
app.delegate = notifier
app.setActivationPolicy(.accessory)
app.run()
