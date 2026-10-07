# The KiwiSDR protocol layer. The pure helpers here stay free of sockets and
# devices so the parity suite can drive them headlessly; the live QWebSocket
# session builds on top of them.
add_library(ituner_transport STATIC
    kiwi_transport.cpp
    kiwi_transport.h
    live_state.cpp
    live_state.h
)

target_include_directories(ituner_transport
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(ituner_transport
    PUBLIC Qt6::Core
    PRIVATE ituner_core
)

set_target_properties(ituner_transport PROPERTIES
    POSITION_INDEPENDENT_CODE ON
)
