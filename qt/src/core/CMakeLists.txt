# The domain core: no Qt GUI, no sockets, no audio. It must stay buildable and
# testable on a desktop with no display attached. Files are read through QtCore
# only (`QFile`/`QJsonDocument`), so the module never needs a platform plugin.
add_library(ituner_core STATIC
    audio_controls.cpp
    audio_controls.h
    display_geometry.cpp
    display_geometry.h
    draw_list.cpp
    draw_list.h
    drawer_bodies.cpp
    drawer_bodies.h
    drawer_geometry.cpp
    drawer_geometry.h
    menu_icons.cpp
    menu_icons.h
    navigation.cpp
    navigation.h
    passband_overlay.cpp
    passband_overlay.h
    receiver_capabilities.cpp
    receiver_capabilities.h
    receiver_catalog.cpp
    receiver_catalog.h
    spectrum_model.cpp
    spectrum_model.h
    state_store.cpp
    state_store.h
    swipe_gesture.cpp
    swipe_gesture.h
    tuning.cpp
    tuning.h
    waterfall_controls.cpp
    waterfall_controls.h
    waterfall_model.cpp
    waterfall_model.h
    waterfall_palette.cpp
    waterfall_palette.h
)

target_include_directories(ituner_core
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(ituner_core
    PUBLIC Qt6::Core
)

set_target_properties(ituner_core PROPERTIES
    POSITION_INDEPENDENT_CODE ON
)
