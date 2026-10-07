# The Qt Quick scene-graph items.
#
# This is the only module in the port that needs a renderer. It owns no domain
# logic: every colour, coordinate and rule it draws comes from `ituner_core`, and
# the core is what the goldens verify. Keeping that boundary is what lets the
# display behaviour be checked without a device.
add_library(ituner_ui STATIC
    overlay_item.cpp
    overlay_item.h
    waterfall_item.cpp
    waterfall_item.h
    waterfall_view.cpp
    waterfall_view.h
)

target_include_directories(ituner_ui
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(ituner_ui
    PUBLIC ituner_core
    PUBLIC Qt6::Core
    PUBLIC Qt6::Gui
    PUBLIC Qt6::Quick
)

set_target_properties(ituner_ui PROPERTIES
    POSITION_INDEPENDENT_CODE ON
)
