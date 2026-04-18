Feature: Web adapter plugin

  The bundled web adapter plugin loads via the standard plugin ABI,
  serves a pre-built UI shell plus a WebSocket bridge on 127.0.0.1,
  and translates browser frames into kernel events and kernel events
  back into browser frames with per-connection session routing.

  Scenarios mirror specs/plugin-web.spec Completion Criteria and are
  kept in sync by scripts/check_feature_sync.py.

  Scenario: Web adapter exposes a WebSocket on 127.0.0.1
    Given a loaded web adapter plugin
    When a websocket client connects to the ws route
    Then the connection is accepted and bound to 127.0.0.1

  Scenario: Browser user message round-trips as user.message.received
    Given a loaded web adapter plugin with a websocket client connected
    When the client sends a user.message frame carrying text hi
    Then a user.message.received event is observed on the bus with text hi

  Scenario: Assistant delta from the bus reaches the browser
    Given a loaded web adapter plugin with a websocket client connected on a session
    When an assistant.message.delta event is published for that session
    Then the client receives an assistant.delta frame with the same content

  Scenario: Kernel-origin events broadcast to every connected client
    Given a loaded web adapter plugin with two websocket clients connected
    When a kernel.ready event is published on the kernel session
    Then both clients receive the kernel.ready frame

  Scenario: GET api plugins returns the adapter cached snapshot
    Given a loaded web adapter plugin that observed a plugin.loaded event
    When a client issues a GET request to api plugins
    Then the response body carries a plugins list with the observed row

  Scenario: on_unload stops uvicorn within the timeout
    Given a loaded web adapter plugin with an active uvicorn server
    When on_unload is awaited
    Then the uvicorn server task completes and clients are closed
