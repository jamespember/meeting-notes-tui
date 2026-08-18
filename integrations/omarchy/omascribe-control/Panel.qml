import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

// Omascribe control panel — live recording status, quick actions, and the
// most recent meeting notes, in the Quattro menu layout concept: a hero, a
// section of icon rows, and a list of notes. Status and note metadata come
// from `omascribe-panel`, which reads the app's runtime status file and the
// notes directory from the Omascribe config.

Panel {
  id: root
  moduleName: "omascribe.control"
  ipcTarget: "omascribe.control"

  readonly property color hoverFill: bar
    ? Style.hoverFillFor(bar.foreground, Color.accent)
    : Style.hoverFill
  readonly property color selectedFill: bar
    ? Style.selectedFillFor(bar.foreground, Color.accent)
    : Style.selectedFill

  property var info: ({})  // { status: {state, duration}, notes_dir, config_path, recent: [...] }

  readonly property string state: info && info.status && info.status.state ? String(info.status.state) : "ready"
  readonly property string duration: info && info.status ? String(info.status.duration || "") : ""
  readonly property var recent: info && info.recent ? info.recent : []
  readonly property int refreshInterval: Math.max(1, root.setting("refreshIntervalSec", 1))
  readonly property int maxRecent: Math.max(0, root.setting("maxRecent", 6))

  readonly property string stateLabel: {
    switch (root.state) {
      case "recording": return "RECORDING"
      case "processing": return "PROCESSING"
      default: return "READY"
    }
  }

  function barText() {
    if (root.state === "recording") return "󰦕 " + root.duration
    if (root.state === "processing") return "󰄬"
    return "󰗠"
  }

  function barTooltip() {
    if (root.state === "recording") return "Omascribe — recording " + root.duration
    if (root.state === "processing") return "Omascribe — processing recording"
    return "Omascribe — ready"
  }

  function applyData(raw) {
    var text = String(raw || "").trim()
    if (!text) return
    try {
      root.info = JSON.parse(text)
    } catch (e) {
      root.info = ({})
    }
    clampCursor()
  }

  // Single cursor model shared by keyboard and mouse. Sections:
  //   "actions" — quick action rows
  //   "recent"  — recent meeting notes
  property string focusSection: "actions"
  property int selectedIndex: -1
  property bool cursorActive: false

  readonly property var visibleSections: (["actions"]).concat(root.recent.length > 0 ? ["recent"] : [])

  function sectionCount(section) {
    if (section === "actions") return 3
    if (section === "recent") return Math.min(root.recent.length, root.maxRecent)
    return 0
  }

  function moveCursor(delta) {
    if (!root.opened) return
    if (!root.cursorActive) { root.cursorActive = true; selectedIndex = 0; return }
    var sections = root.visibleSections
    var idx = sections.indexOf(root.focusSection)
    var count = root.sectionCount(root.focusSection)
    var next = root.selectedIndex + delta
    if (next >= 0 && next < count) { root.selectedIndex = next; return }
    var targetIdx = idx + delta
    if (targetIdx >= 0 && targetIdx < sections.length) {
      root.focusSection = sections[targetIdx]
      root.selectedIndex = 0
    }
  }

  function activateCursor() {
    if (!root.cursorActive) return
    if (root.focusSection === "actions") {
      if (root.selectedIndex === 0) root.launchTui()
      else if (root.selectedIndex === 1) root.openNotesFolder()
      else if (root.selectedIndex === 2) root.openSettings()
    } else if (root.focusSection === "recent") {
      var items = root.recent.slice(0, root.maxRecent)
      if (root.selectedIndex >= 0 && root.selectedIndex < items.length) root.openNote(items[root.selectedIndex])
    }
  }

  function clampCursor() {
    var sections = root.visibleSections
    if (!sections.length) return
    if (sections.indexOf(root.focusSection) < 0) {
      root.focusSection = sections[0]
      root.selectedIndex = 0
      return
    }
    var count = root.sectionCount(root.focusSection)
    if (root.selectedIndex > count - 1) root.selectedIndex = Math.max(0, count - 1)
    if (root.selectedIndex < 0) root.selectedIndex = 0
  }

  function ensureCursorVisible(item) {
    if (!item || !scrollArea) return
    var flick = scrollArea.contentItem
    if (!flick || flick.contentY === undefined) return
    var margin = 6
    var maxY = Math.max(0, (flick.contentHeight || 0) - flick.height)
    if (maxY <= Style.space(24)) { flick.contentY = 0; return }
    var pt = item.mapToItem(flick.contentItem || flick, 0, 0)
    var top = pt.y
    var bottom = top + (item.height || 0)
    var viewTop = flick.contentY
    var viewBottom = viewTop + flick.height
    if (top < viewTop + margin) flick.contentY = Math.max(0, Math.min(maxY, top - margin))
    else if (bottom > viewBottom - margin)
      flick.contentY = Math.max(0, Math.min(maxY, bottom + margin - flick.height))
  }

  function launchTui() {
    Util.execDetached("omarchy-launch-or-focus-tui omascribe")
  }

  function openNotesFolder() {
    var dir = root.info && root.info.notes_dir ? String(root.info.notes_dir) : ""
    if (!dir) { root.launchTui(); return }
    Util.execDetached("xdg-open " + Util.shellQuote(dir))
  }

  function openSettings() {
    var path = root.info && root.info.config_path ? String(root.info.config_path) : ""
    Util.execDetached("omarchy-launch-editor " + Util.shellQuote(path || "$HOME/.config/omascribe/config.yaml"))
  }

  function openNote(note) {
    if (!note) return
    var path = String(note.path || "")
    if (path) Util.execDetached("omarchy-launch-editor " + Util.shellQuote(path))
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Process {
    id: dataProc
    command: ["omascribe-panel"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyData(text)
    }
  }

  Timer {
    interval: root.refreshInterval * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: if (!dataProc.running) dataProc.running = true
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.barText()
    tooltipText: root.barTooltip()
    onPressed: function(b) {
      if (b === Qt.RightButton) root.launchTui()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(panelColumn.implicitHeight, Style.space(520))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        if (dy !== 0) root.moveCursor(dy)
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      ScrollView {
        id: scrollArea
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: panelColumn.implicitHeight > height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
        Binding {
          target: scrollArea.contentItem
          property: "interactive"
          value: panelColumn.implicitHeight > scrollArea.height
        }

        Column {
          id: panelColumn
          width: scrollArea.availableWidth
          spacing: Style.space(14)

          // ---------- Hero: glyph · Omascribe / state ----------
          Item {
            id: heroItem
            width: parent.width
            implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight)

            Text {
              id: heroIcon
              text: root.barText()
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.display
              opacity: root.state === "ready" ? 1.0 : 1.0
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
            }

            Column {
              id: heroLabels
              anchors.left: heroIcon.right
              anchors.leftMargin: Style.space(14)
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)

              Text {
                text: "Omascribe"
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.title
                font.bold: true
                elide: Text.ElideRight
                width: parent.width
              }

              Text {
                id: heroMeta
                text: root.stateLabel + (root.state === "recording" && root.duration !== "" ? " · " + root.duration : "")
                color: Qt.darker(root.bar.foreground, 1.4)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1.2
                elide: Text.ElideRight
                width: parent.width
              }
            }
          }

          // ---- Quick actions ----
          PanelSeparator {
            foreground: root.bar.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(6)

            PanelSectionHeader {
              text: "ACTIONS"
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
            }

            ActionRow {
              width: panelColumn.width
              rowIndex: 0
              glyph: "󰗠"
              label: "Launch Omascribe"
              meta: root.state === "recording" ? "open to stop" : "record a meeting"
              onActivated: root.launchTui()
            }

            ActionRow {
              width: panelColumn.width
              rowIndex: 1
              glyph: "󰉋"
              label: "Open notes folder"
              meta: root.info && root.info.notes_dir ? String(root.info.notes_dir) : ""
              onActivated: root.openNotesFolder()
            }

            ActionRow {
              width: panelColumn.width
              rowIndex: 2
              glyph: "󰒓"
              label: "Settings"
              meta: "edit config.yaml"
              onActivated: root.openSettings()
            }
          }

          // ---- Recent meetings ----
          PanelSeparator {
            visible: root.recent.length > 0
            foreground: root.bar.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(10)
            visible: root.recent.length > 0

            PanelSectionHeader {
              text: "RECENT MEETINGS"
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
            }

            Repeater {
              model: root.recent.slice(0, root.maxRecent)

              NoteRow {
                required property var modelData
                required property int index
                width: panelColumn.width
                note: modelData
                rowIndex: index
              }
            }
          }
        }
      }
    }
  }

  // ---- Reusable inline components ----

  component ActionRow: CursorSurface {
    id: actionRow
    required property int rowIndex
    property string glyph: ""
    property string label: ""
    property string meta: ""

    signal activated()

    hasCursor: root.cursorActive && root.focusSection === "actions" && root.selectedIndex === rowIndex
    onHasCursorChanged: if (hasCursor) root.ensureCursorVisible(actionRow)
    foreground: root.bar.foreground
    fill: root.hoverFill
    currentFill: root.selectedFill
    implicitHeight: actionInner.implicitHeight + Style.spacing.xl

    Row {
      id: actionInner
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(6)
      anchors.rightMargin: Style.space(6)
      spacing: Style.space(8)

      Text {
        text: actionRow.glyph
        color: root.bar.foreground
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.title
        width: Style.space(22)
        horizontalAlignment: Text.AlignHCenter
        anchors.verticalCenter: parent.verticalCenter
      }

      Column {
        width: parent.width - Style.space(22) - Style.space(8)
        spacing: Style.space(1)
        anchors.verticalCenter: parent.verticalCenter

        Text {
          text: actionRow.label
          color: root.bar.foreground
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
          width: parent.width
        }

        Text {
          text: actionRow.meta
          visible: text !== ""
          color: Qt.darker(root.bar.foreground, 1.4)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
          width: parent.width
        }
      }
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onContainsMouseChanged: if (containsMouse) {
        root.cursorActive = true
        root.focusSection = "actions"
        root.selectedIndex = actionRow.rowIndex
      }
      onClicked: actionRow.activated()
    }
  }

  component NoteRow: CursorSurface {
    id: noteRow
    required property var note
    required property int rowIndex

    hasCursor: root.cursorActive && root.focusSection === "recent" && root.selectedIndex === rowIndex
    onHasCursorChanged: if (hasCursor) root.ensureCursorVisible(noteRow)
    foreground: root.bar.foreground
    fill: root.hoverFill
    currentFill: root.selectedFill
    implicitHeight: noteInner.implicitHeight + Style.spacing.xl

    Row {
      id: noteInner
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(6)
      anchors.rightMargin: Style.space(6)
      spacing: Style.space(8)

      Text {
        text: "󰈮"
        color: root.bar.foreground
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.title
        width: Style.space(22)
        horizontalAlignment: Text.AlignHCenter
        anchors.verticalCenter: parent.verticalCenter
      }

      Column {
        width: parent.width - Style.space(22) - Style.space(8)
        spacing: Style.space(1)
        anchors.verticalCenter: parent.verticalCenter

        Text {
          text: String(noteRow.note.title || "")
          color: root.bar.foreground
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.body
          font.bold: false
          elide: Text.ElideRight
          width: parent.width
        }

        Text {
          text: (noteRow.note.date ? String(noteRow.note.date) : "") +
                (noteRow.note.words ? " · " + String(noteRow.note.words) + " words" : "")
          color: Qt.darker(root.bar.foreground, 1.4)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
          width: parent.width
        }
      }
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onContainsMouseChanged: if (containsMouse) {
        root.cursorActive = true
        root.focusSection = "recent"
        root.selectedIndex = noteRow.rowIndex
      }
      onClicked: root.openNote(noteRow.note)
    }
  }
}
