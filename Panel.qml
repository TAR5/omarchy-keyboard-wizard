import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui

Item {
  id: root

  property string omarchyPath: Quickshell.env("OMARCHY_PATH")
  property var shell: null
  property var manifest: null

  property bool opened: false
  property int page: 0 // 0 loading, 1 setup, 2 modifiers, 3 characters, 4 review, 5 applying, 6 result
  property string captureMode: ""
  readonly property bool captureActive: captureMode !== ""
  readonly property string pluginId: manifest && manifest.id ? String(manifest.id) : "tar5.keyboard-wizard"
  readonly property string backendPath: manifest && manifest.__sourceDir
    ? String(manifest.__sourceDir).replace(/\/$/, "") + "/src/backend.py"
    : ""

  property var stateData: ({})
  property var deviceOptions: []
  property var layoutOptions: []
  property var variantOptions: []
  property var secondaryOptions: []
  property var switchOptions: []
  property var modifierSteps: []
  property var characterSteps: []

  property string deviceName: ""
  property string deviceDescription: ""
  property string primaryLayout: ""
  property string primaryVariant: ""
  property string secondaryLayout: ""
  property string switchOption: ""

  property int modifierIndex: 0
  property int characterIndex: 0
  property var modifierCaptures: ({})
  property var characterCaptures: ({})
  property bool pressPending: false
  property var pendingModifier: null
  property var heldKeys: ({})
  property int heldCount: 0
  property var matchedCharacter: null
  property string detectedText: ""

  property var reviewData: ({})
  property var resultData: ({})
  property bool applying: false
  property string processError: ""
  property bool stateHandled: false
  property bool reviewHandled: false
  property bool applyHandled: false

  readonly property var currentModifier: modifierIndex >= 0 && modifierIndex < modifierSteps.length
    ? modifierSteps[modifierIndex] : ({})
  readonly property var currentCharacter: characterIndex >= 0 && characterIndex < characterSteps.length
    ? characterSteps[characterIndex] : ({})
  readonly property real modifierProgress: modifierSteps.length > 0
    ? modifierIndex / modifierSteps.length : 0
  readonly property real characterProgress: characterSteps.length > 0
    ? characterIndex / characterSteps.length : 0

  function copyWithValue(source, key, value) {
    var next = ({})
    var input = source || {}
    for (var existing in input) next[existing] = input[existing]
    next[key] = value
    return next
  }

  function withoutKey(source, key) {
    var next = ({})
    var input = source || {}
    for (var existing in input) if (existing !== key) next[existing] = input[existing]
    return next
  }

  function parseBackend(raw, fallbackTitle) {
    try {
      var parsed = JSON.parse(String(raw || "{}"))
      if (!parsed || typeof parsed !== "object") throw new Error("Backend returned no object")
      return parsed
    } catch (error) {
      return {
        ok: false,
        title: fallbackTitle || "Backend error",
        message: "Could not parse the backend response: " + error
      }
    }
  }

  function fail(title, message) {
    captureMode = ""
    applying = false
    resultData = {
      ok: false,
      warning: false,
      title: title || "Keyboard Setup error",
      message: message || "An unknown error occurred."
    }
    page = 6
    focusCard()
  }

  function focusCard() {
    Qt.callLater(function() {
      if (root.opened) card.forceActiveFocus()
    })
  }

  function resetHeldKeys() {
    heldKeys = ({})
    heldCount = 0
    matchedCharacter = null
  }

  function resetWizard() {
    page = 0
    captureMode = ""
    stateData = ({})
    reviewData = ({})
    resultData = ({})
    modifierCaptures = ({})
    characterCaptures = ({})
    modifierIndex = 0
    characterIndex = 0
    pressPending = false
    pendingModifier = null
    detectedText = ""
    processError = ""
    stateHandled = false
    reviewHandled = false
    applyHandled = false
    resetHeldKeys()
  }

  function open(payloadJson) {
    if (applyProc.running) {
      opened = true
      page = 5
      applying = true
      focusCard()
      return
    }
    resetWizard()
    opened = true
    requestState()
    focusCard()
  }

  function close() {
    opened = false
    captureMode = ""
    pressPending = false
    pendingModifier = null
    resetHeldKeys()
    if (stateProc.running) stateProc.running = false
    if (reviewProc.running) reviewProc.running = false
  }

  function dismiss() {
    if (applying) return
    if (shell && typeof shell.hide === "function") shell.hide(pluginId)
    else close()
  }

  function requestState() {
    if (!backendPath) {
      fail("Backend unavailable", "The plugin source directory was not provided by Omarchy.")
      return
    }
    stateHandled = false
    processError = ""
    stateProc.command = ["python3", backendPath, "state"]
    stateProc.running = true
  }

  function handleState(raw) {
    stateHandled = true
    var data = parseBackend(raw, "Could not load keyboard state")
    if (!data.ok) {
      fail(data.title, data.message)
      return
    }
    stateData = data
    modifierSteps = data.modifier_steps || []
    characterSteps = data.character_steps || []
    layoutOptions = data.layouts || []
    switchOptions = data.switch_options || []
    deviceOptions = []
    var devices = data.devices || []
    for (var i = 0; i < devices.length; i++) {
      deviceOptions.push({
        value: String(devices[i].name),
        label: String(devices[i].description || devices[i].name) + " — " + String(devices[i].name)
      })
    }
    secondaryOptions = [{ value: "", label: "None" }].concat(layoutOptions)

    if (deviceOptions.length === 0) {
      fail(
        "No physical keyboards found",
        "Open the wizard inside the active Omarchy desktop session and make sure the keyboard appears in `hyprctl -j devices`."
      )
      return
    }
    deviceName = String(deviceOptions[0].value)
    syncDeviceDescription()
    primaryLayout = optionExists(layoutOptions, String(data.current_layout || ""))
      ? String(data.current_layout) : String(layoutOptions.length ? layoutOptions[0].value : "us")
    primaryVariant = ""
    secondaryLayout = ""
    switchOption = String(switchOptions.length ? switchOptions[0].value : "grp:alt_shift_toggle")
    rebuildVariantOptions()
    page = 1
    focusCard()
  }

  function optionExists(options, value) {
    var choices = options || []
    for (var i = 0; i < choices.length; i++)
      if (String(choices[i].value) === String(value)) return true
    return false
  }

  function syncDeviceDescription() {
    var devices = stateData.devices || []
    deviceDescription = deviceName
    for (var i = 0; i < devices.length; i++) {
      if (String(devices[i].name) === deviceName) {
        deviceDescription = String(devices[i].description || devices[i].name)
        return
      }
    }
  }

  function rebuildVariantOptions() {
    var byLayout = stateData.variants || {}
    variantOptions = [{ value: "", label: "Default" }].concat(byLayout[primaryLayout] || [])
    if (!optionExists(variantOptions, primaryVariant)) primaryVariant = ""
  }

  function startModifiers() {
    modifierCaptures = ({})
    characterCaptures = ({})
    modifierIndex = 0
    characterIndex = 0
    captureMode = "modifier"
    page = 2
    pressPending = false
    pendingModifier = null
    detectedText = "Waiting for a key press…"
    resetHeldKeys()
    focusCard()
  }

  function startCharacters() {
    captureMode = "character"
    page = 3
    characterIndex = 0
    detectedText = "Waiting for the character…"
    resetHeldKeys()
    focusCard()
  }

  function restartCapture() {
    startModifiers()
  }

  function handleKeyPressed(event) {
    if (!captureActive) {
      if (event.key === Qt.Key_Escape) {
        dismiss()
        event.accepted = true
      }
      return
    }

    event.accepted = true
    if (event.isAutoRepeat) return

    if (captureMode === "modifier") {
      if (pressPending) return
      pressPending = true
      pendingModifier = {
        keycode: Number(event.nativeScanCode),
        key: Number(event.key),
        modifiers: Number(event.modifiers)
      }
      detectedText = "Detected scan code " + Number(event.nativeScanCode) + ". Release to continue…"
      return
    }

    if (captureMode === "character") {
      var heldKey = String(Number(event.nativeScanCode))
      if (!heldKeys[heldKey]) {
        heldKeys = copyWithValue(heldKeys, heldKey, true)
        heldCount += 1
      }
      var produced = String(event.text || "")
      if (!produced) return
      var expected = String(currentCharacter.character || "")
      if (produced.indexOf(expected) !== -1) {
        matchedCharacter = {
          actual: produced,
          keycode: Number(event.nativeScanCode),
          key: Number(event.key),
          modifiers: Number(event.modifiers)
        }
        detectedText = "Detected “" + printableCharacter(produced) + "”. Release the keys to continue…"
      } else {
        detectedText = "Detected “" + printableCharacter(produced) + "” — try again."
      }
    }
  }

  function handleKeyReleased(event) {
    if (!captureActive) return
    event.accepted = true

    if (captureMode === "modifier") {
      if (!pressPending || !pendingModifier) return
      modifierCaptures = copyWithValue(modifierCaptures, String(currentModifier.key), pendingModifier)
      pressPending = false
      pendingModifier = null
      advanceModifier()
      return
    }

    if (captureMode === "character") {
      var heldKey = String(Number(event.nativeScanCode))
      if (heldKeys[heldKey]) {
        heldKeys = withoutKey(heldKeys, heldKey)
        heldCount = Math.max(0, heldCount - 1)
      }
      if (matchedCharacter && heldCount === 0) {
        characterCaptures = copyWithValue(
          characterCaptures,
          String(currentCharacter.key),
          matchedCharacter
        )
        matchedCharacter = null
        advanceCharacter()
      }
    }
  }

  function advanceModifier() {
    if (modifierIndex + 1 >= modifierSteps.length) {
      startCharacters()
      return
    }
    modifierIndex += 1
    detectedText = "Waiting for a key press…"
    focusCard()
  }

  function advanceCharacter() {
    resetHeldKeys()
    if (characterIndex + 1 >= characterSteps.length) {
      requestReview()
      return
    }
    characterIndex += 1
    detectedText = "Waiting for the character…"
    focusCard()
  }

  function skipCurrent() {
    if (captureMode === "modifier") {
      modifierCaptures = copyWithValue(
        modifierCaptures,
        String(currentModifier.key),
        { skipped: true }
      )
      pressPending = false
      pendingModifier = null
      advanceModifier()
    } else if (captureMode === "character") {
      characterCaptures = copyWithValue(
        characterCaptures,
        String(currentCharacter.key),
        { skipped: true, actual: "" }
      )
      advanceCharacter()
    }
  }

  function printableCharacter(value) {
    var text = String(value || "")
    if (text === "\\") return "backslash"
    if (text === " ") return "Space"
    return text.replace(/\n/g, "\\n").replace(/\t/g, "\\t")
  }

  function configurationPayload() {
    return {
      device_name: deviceName,
      primary_layout: primaryLayout,
      primary_variant: primaryVariant,
      secondary_layout: secondaryLayout,
      switch_option: switchOption,
      captures: {
        modifiers: modifierCaptures,
        characters: characterCaptures
      }
    }
  }

  function requestReview() {
    captureMode = ""
    resetHeldKeys()
    page = 4
    reviewData = ({})
    reviewHandled = false
    processError = ""
    reviewProc.command = ["python3", backendPath, "review", JSON.stringify(configurationPayload())]
    reviewProc.running = true
    focusCard()
  }

  function handleReview(raw) {
    reviewHandled = true
    var data = parseBackend(raw, "Could not review the captures")
    if (!data.ok) {
      fail(data.title, data.message)
      return
    }
    reviewData = data
  }

  function applyConfiguration() {
    applying = true
    page = 5
    applyHandled = false
    processError = ""
    applyProc.command = ["python3", backendPath, "apply", JSON.stringify(configurationPayload())]
    applyProc.running = true
    focusCard()
  }

  function handleApply(raw) {
    applyHandled = true
    applying = false
    var data = parseBackend(raw, "Could not apply the configuration")
    resultData = data
    page = 6
    focusCard()
  }

  Process {
    id: stateProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.handleState(text)
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.processError = String(text || "").trim()
    }
    onExited: function(exitCode) {
      if (exitCode !== 0 && !root.stateHandled)
        root.fail("Could not load keyboard state", root.processError || "Backend exited with code " + exitCode)
    }
  }

  Process {
    id: reviewProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.handleReview(text)
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.processError = String(text || "").trim()
    }
    onExited: function(exitCode) {
      if (exitCode !== 0 && !root.reviewHandled)
        root.fail("Could not review the captures", root.processError || "Backend exited with code " + exitCode)
    }
  }

  Process {
    id: applyProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.handleApply(text)
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.processError = String(text || "").trim()
    }
    onExited: function(exitCode) {
      if (exitCode !== 0 && !root.applyHandled) {
        root.applying = false
        root.fail("Could not apply the configuration", root.processError || "Backend exited with code " + exitCode)
      }
    }
  }

  PanelWindow {
    id: panel
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "omarchy-keyboard-wizard"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: root.opened
      ? WlrKeyboardFocus.Exclusive
      : WlrKeyboardFocus.None

    Rectangle {
      anchors.fill: parent
      color: Util.alpha(Color.background, 0.78)

      MouseArea {
        anchors.fill: parent
        onClicked: root.dismiss()
      }
    }

    BorderSurface {
      id: card
      anchors.centerIn: parent
      width: Math.min(Style.space(780), panel.width - Style.space(32))
      height: Math.min(Style.space(700), panel.height - Style.space(32))
      color: Color.popups.background
      borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.normalBorderWidth))
      radius: Style.cornerRadius
      focus: true

      Keys.priority: Keys.BeforeItem
      Keys.onPressed: function(event) { root.handleKeyPressed(event) }
      Keys.onReleased: function(event) { root.handleKeyReleased(event) }

      MouseArea { anchors.fill: parent; onClicked: {} }

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: Style.spacing.panelPadding
        spacing: Style.spacing.panelGap

        RowLayout {
          Layout.fillWidth: true
          spacing: Style.spacing.md

          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.spacing.xxs

            Text {
              text: "KEYBOARD SETUP"
              color: Color.popups.text
              font.family: Style.font.family
              font.pixelSize: Style.font.heading
              font.bold: true
            }
            Text {
              text: "Quickshell calibration for Omarchy"
              color: Util.alpha(Color.popups.text, 0.62)
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
            }
          }

          Button {
            text: root.applying ? "Applying…" : "Close"
            enabled: !root.applying
            bordered: true
            onClicked: root.dismiss()
          }
        }

        Rectangle {
          Layout.fillWidth: true
          Layout.preferredHeight: Math.max(1, Style.spacing.hairline)
          color: Util.alpha(Color.popups.text, 0.16)
        }

        StackLayout {
          Layout.fillWidth: true
          Layout.fillHeight: true
          currentIndex: root.page

          Item {
            ColumnLayout {
              anchors.centerIn: parent
              width: Math.min(parent.width, Style.space(460))
              spacing: Style.spacing.huge

              Text {
                Layout.alignment: Qt.AlignHCenter
                text: "󰌌"
                color: Color.accent
                font.family: Style.font.family
                font.pixelSize: Style.space(56)
              }
              Text {
                Layout.fillWidth: true
                text: "Reading keyboards and XKB layouts…"
                color: Color.popups.text
                horizontalAlignment: Text.AlignHCenter
                font.family: Style.font.family
                font.pixelSize: Style.font.title
              }
            }
          }

          Item {
            Flickable {
              anchors.fill: parent
              contentWidth: width
              contentHeight: setupColumn.implicitHeight
              clip: true
              boundsBehavior: Flickable.StopAtBounds

              ColumnLayout {
                id: setupColumn
                width: parent.width
                spacing: Style.spacing.xl

                Text {
                  Layout.fillWidth: true
                  text: "Choose a layout, then use only the selected physical keyboard during calibration."
                  color: Color.popups.text
                  wrapMode: Text.WordWrap
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                }

                Dropdown {
                  Layout.fillWidth: true
                  Layout.preferredWidth: setupColumn.width
                  label: "Physical keyboard"
                  options: root.deviceOptions
                  value: root.deviceName
                  onChanged: function(value) {
                    root.deviceName = value
                    root.syncDeviceDescription()
                  }
                }

                GridLayout {
                  Layout.fillWidth: true
                  columns: width > Style.space(620) ? 2 : 1
                  columnSpacing: Style.spacing.xl
                  rowSpacing: Style.spacing.lg

                  Dropdown {
                    Layout.fillWidth: true
                    label: "Primary layout"
                    options: root.layoutOptions
                    value: root.primaryLayout
                    onChanged: function(value) {
                      root.primaryLayout = value
                      root.primaryVariant = ""
                      root.rebuildVariantOptions()
                    }
                  }
                  Dropdown {
                    Layout.fillWidth: true
                    label: "Variant"
                    options: root.variantOptions
                    value: root.primaryVariant
                    onChanged: function(value) { root.primaryVariant = value }
                  }
                  Dropdown {
                    Layout.fillWidth: true
                    label: "Secondary layout"
                    options: root.secondaryOptions
                    value: root.secondaryLayout
                    onChanged: function(value) { root.secondaryLayout = value }
                  }
                  Dropdown {
                    Layout.fillWidth: true
                    visible: root.secondaryLayout !== ""
                    label: "Switch layouts"
                    options: root.switchOptions
                    value: root.switchOption
                    onChanged: function(value) { root.switchOption = value }
                  }
                }

                Text {
                  Layout.fillWidth: true
                  text: "The wizard records physical scan codes for modifiers, then verifies @ { } [ ] > < | ~ and backslash as actual text output."
                  color: Util.alpha(Color.popups.text, 0.66)
                  wrapMode: Text.WordWrap
                  font.family: Style.font.family
                  font.pixelSize: Style.font.bodySmall
                }

                RowLayout {
                  Layout.fillWidth: true
                  Layout.topMargin: Style.spacing.lg
                  Item { Layout.fillWidth: true }
                  Button {
                    text: "Start calibration"
                    active: true
                    bordered: true
                    onClicked: root.startModifiers()
                  }
                }
              }
            }
          }

          Item {
            ColumnLayout {
              anchors.fill: parent
              spacing: Style.spacing.huge

              Text {
                Layout.fillWidth: true
                text: "PHYSICAL KEYS  ·  " + (root.modifierIndex + 1) + " / " + root.modifierSteps.length
                color: Util.alpha(Color.popups.text, 0.62)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                font.bold: true
                horizontalAlignment: Text.AlignHCenter
              }

              Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: Style.spacing.sm
                radius: height / 2
                color: Util.alpha(Color.popups.text, 0.12)
                Rectangle {
                  width: parent.width * root.modifierProgress
                  height: parent.height
                  radius: height / 2
                  color: Color.accent
                }
              }

              Item { Layout.fillHeight: true }

              BorderSurface {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: Math.min(Style.space(420), card.width - Style.space(80))
                Layout.preferredHeight: Style.space(170)
                color: Style.selectedFillFor(Color.popups.text, Color.accent)
                borderSpec: Border.controlSpec("selected", Color.popups.text, Color.accent)
                radius: Style.cornerRadius

                ColumnLayout {
                  anchors.centerIn: parent
                  width: parent.width - Style.space(32)
                  spacing: Style.spacing.md
                  Text {
                    Layout.fillWidth: true
                    text: root.currentModifier.label || "Key"
                    color: Color.popups.text
                    horizontalAlignment: Text.AlignHCenter
                    font.family: Style.font.family
                    font.pixelSize: Style.font.display
                    font.bold: true
                  }
                  Text {
                    Layout.fillWidth: true
                    text: root.currentModifier.hint || "Press the requested key"
                    color: Util.alpha(Color.popups.text, 0.68)
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                  }
                }
              }

              Text {
                Layout.fillWidth: true
                text: root.detectedText
                color: Color.popups.text
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                font.family: Style.font.family
                font.pixelSize: Style.font.body
              }

              Item { Layout.fillHeight: true }

              RowLayout {
                Layout.fillWidth: true
                Button {
                  text: "Cancel"
                  bordered: true
                  onClicked: {
                    root.captureMode = ""
                    root.page = 1
                    root.focusCard()
                  }
                }
                Item { Layout.fillWidth: true }
                Button {
                  text: "Skip this key"
                  bordered: true
                  onClicked: root.skipCurrent()
                }
              }
            }
          }

          Item {
            ColumnLayout {
              anchors.fill: parent
              spacing: Style.spacing.huge

              Text {
                Layout.fillWidth: true
                text: "CHARACTER OUTPUT  ·  " + (root.characterIndex + 1) + " / " + root.characterSteps.length
                color: Util.alpha(Color.popups.text, 0.62)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                font.bold: true
                horizontalAlignment: Text.AlignHCenter
              }

              Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: Style.spacing.sm
                radius: height / 2
                color: Util.alpha(Color.popups.text, 0.12)
                Rectangle {
                  width: parent.width * root.characterProgress
                  height: parent.height
                  radius: height / 2
                  color: Color.accent
                }
              }

              Item { Layout.fillHeight: true }

              BorderSurface {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: Math.min(Style.space(420), card.width - Style.space(80))
                Layout.preferredHeight: Style.space(190)
                color: Style.selectedFillFor(Color.popups.text, Color.accent)
                borderSpec: Border.controlSpec("selected", Color.popups.text, Color.accent)
                radius: Style.cornerRadius

                ColumnLayout {
                  anchors.centerIn: parent
                  width: parent.width - Style.space(32)
                  spacing: Style.spacing.md
                  Text {
                    Layout.fillWidth: true
                    text: root.currentCharacter.label || "Character"
                    color: Color.popups.text
                    horizontalAlignment: Text.AlignHCenter
                    font.family: Style.font.family
                    font.pixelSize: Style.font.displayLarge
                    font.bold: true
                  }
                  Text {
                    Layout.fillWidth: true
                    text: root.currentCharacter.hint || "Type the requested character"
                    color: Util.alpha(Color.popups.text, 0.68)
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                  }
                }
              }

              Text {
                Layout.fillWidth: true
                text: root.detectedText
                color: Color.popups.text
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                font.family: Style.font.family
                font.pixelSize: Style.font.body
              }

              Item { Layout.fillHeight: true }

              RowLayout {
                Layout.fillWidth: true
                Button {
                  text: "Restart"
                  bordered: true
                  onClicked: root.restartCapture()
                }
                Item { Layout.fillWidth: true }
                Button {
                  text: "Skip this character"
                  bordered: true
                  onClicked: root.skipCurrent()
                }
              }
            }
          }

          Item {
            ColumnLayout {
              anchors.fill: parent
              spacing: Style.spacing.lg

              Text {
                Layout.fillWidth: true
                text: "Review configuration"
                color: Color.popups.text
                font.family: Style.font.family
                font.pixelSize: Style.font.heading
                font.bold: true
              }
              Text {
                Layout.fillWidth: true
                text: root.deviceDescription + "  ·  " + root.primaryLayout
                  + (root.primaryVariant ? " (" + root.primaryVariant + ")" : "")
                  + (root.secondaryLayout ? " + " + root.secondaryLayout : "")
                color: Util.alpha(Color.popups.text, 0.65)
                wrapMode: Text.WordWrap
                font.family: Style.font.family
                font.pixelSize: Style.font.body
              }

              Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: width
                contentHeight: reviewColumn.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds

                ColumnLayout {
                  id: reviewColumn
                  width: parent.width
                  spacing: Style.spacing.lg

                  Text {
                    visible: reviewProc.running
                    Layout.fillWidth: true
                    text: "Analyzing scan codes and character output…"
                    color: Color.popups.text
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                  }

                  BorderSurface {
                    visible: !reviewProc.running && root.reviewData.ok === true
                    Layout.fillWidth: true
                    Layout.preferredHeight: reviewSummary.implicitHeight + Style.space(24)
                    color: Style.normalFillFor(Color.popups.text, Color.accent)
                    borderSpec: Border.controlSpec("normal", Color.popups.text, Color.accent)
                    radius: Style.cornerRadius

                    ColumnLayout {
                      id: reviewSummary
                      anchors.left: parent.left
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      anchors.margins: Style.spacing.xxl
                      spacing: Style.spacing.md
                      Text {
                        Layout.fillWidth: true
                        text: root.reviewData.correction_options && root.reviewData.correction_options.length
                          ? "Corrections: " + root.reviewData.correction_options.join(", ")
                          : "Modifier mapping: no supported firmware swaps detected"
                        color: Color.popups.text
                        wrapMode: Text.WordWrap
                        font.family: Style.font.family
                        font.pixelSize: Style.font.body
                      }
                      Text {
                        Layout.fillWidth: true
                        text: "Characters verified: " + Number(root.reviewData.character_pass_count || 0)
                          + " / " + Number(root.reviewData.character_total || 0)
                        color: Color.popups.text
                        font.family: Style.font.family
                        font.pixelSize: Style.font.body
                      }
                    }
                  }

                  Flow {
                    visible: !reviewProc.running
                    Layout.fillWidth: true
                    spacing: Style.spacing.md

                    Repeater {
                      model: root.reviewData.character_results || []
                      delegate: BorderSurface {
                        required property var modelData
                        width: Math.max(Style.space(52), chipText.implicitWidth + Style.space(22))
                        height: Style.space(38)
                        color: modelData.status === "correct"
                          ? Style.selectedFillFor(Color.popups.text, Color.accent)
                          : Util.alpha(Color.urgent, 0.16)
                        borderSpec: Border.controlSpec(
                          modelData.status === "correct" ? "selected" : "normal",
                          modelData.status === "correct" ? Color.popups.text : Color.urgent,
                          Color.accent
                        )
                        radius: Style.cornerRadius
                        Text {
                          id: chipText
                          anchors.centerIn: parent
                          text: String(modelData.label) + (modelData.status === "correct" ? "  ✓" : "  !")
                          color: modelData.status === "correct" ? Color.popups.text : Color.urgent
                          font.family: Style.font.family
                          font.pixelSize: Style.font.body
                        }
                      }
                    }
                  }

                  Text {
                    visible: Boolean(
                      root.reviewData.warnings && root.reviewData.warnings.length > 0
                    )
                    Layout.fillWidth: true
                    text: "Needs attention"
                    color: Color.urgent
                    font.family: Style.font.family
                    font.pixelSize: Style.font.title
                    font.bold: true
                  }

                  Repeater {
                    model: root.reviewData.warnings || []
                    delegate: Text {
                      required property var modelData
                      Layout.fillWidth: true
                      text: "• " + String(modelData)
                      color: Color.popups.text
                      wrapMode: Text.WordWrap
                      font.family: Style.font.family
                      font.pixelSize: Style.font.bodySmall
                    }
                  }
                }
              }

              RowLayout {
                Layout.fillWidth: true
                Button {
                  text: "Scan again"
                  bordered: true
                  enabled: !reviewProc.running
                  onClicked: root.restartCapture()
                }
                Item { Layout.fillWidth: true }
                Button {
                  text: "Apply configuration"
                  active: true
                  bordered: true
                  enabled: !reviewProc.running && root.reviewData.ok === true
                  onClicked: root.applyConfiguration()
                }
              }
            }
          }

          Item {
            ColumnLayout {
              anchors.centerIn: parent
              width: Math.min(parent.width, Style.space(500))
              spacing: Style.spacing.huge
              Text {
                Layout.fillWidth: true
                text: "Applying keyboard configuration…"
                color: Color.popups.text
                horizontalAlignment: Text.AlignHCenter
                font.family: Style.font.family
                font.pixelSize: Style.font.heading
                font.bold: true
              }
              Text {
                Layout.fillWidth: true
                text: "Creating a backup, writing the device override, reloading Hyprland, and checking configerrors."
                color: Util.alpha(Color.popups.text, 0.66)
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                font.family: Style.font.family
                font.pixelSize: Style.font.body
              }
            }
          }

          Item {
            ColumnLayout {
              anchors.centerIn: parent
              width: Math.min(parent.width, Style.space(560))
              spacing: Style.spacing.huge

              Text {
                Layout.alignment: Qt.AlignHCenter
                text: root.resultData.ok ? (root.resultData.warning ? "!" : "✓") : "×"
                color: root.resultData.ok && !root.resultData.warning ? Color.accent : Color.urgent
                font.family: Style.font.family
                font.pixelSize: Style.space(52)
                font.bold: true
              }
              Text {
                Layout.fillWidth: true
                text: root.resultData.title || "Keyboard Setup"
                color: Color.popups.text
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                font.family: Style.font.family
                font.pixelSize: Style.font.heading
                font.bold: true
              }
              Text {
                Layout.fillWidth: true
                text: root.resultData.message || ""
                color: Util.alpha(Color.popups.text, 0.72)
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                font.family: Style.font.family
                font.pixelSize: Style.font.body
              }
              Text {
                visible: root.resultData.backup !== undefined
                Layout.fillWidth: true
                text: root.resultData.backup ? "Backup: " + root.resultData.backup : ""
                color: Util.alpha(Color.popups.text, 0.55)
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WrapAnywhere
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
              RowLayout {
                Layout.fillWidth: true
                Button {
                  text: "Set up another keyboard"
                  bordered: true
                  onClicked: root.requestState()
                }
                Item { Layout.fillWidth: true }
                Button {
                  text: "Done"
                  active: true
                  bordered: true
                  onClicked: root.dismiss()
                }
              }
            }
          }
        }
      }
    }
  }
}
