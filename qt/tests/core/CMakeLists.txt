qt_add_executable(tst_display_geometry
    tst_display_geometry.cpp
)

target_link_libraries(tst_display_geometry
    PRIVATE
        ituner_core
        Qt6::Test
)

if(NOT MSVC)
    target_compile_options(tst_display_geometry PRIVATE -Wall -Wextra)
endif()

add_test(NAME display_geometry COMMAND tst_display_geometry)

# The catalog parity test reads the fixtures and the goldens captured from the
# Python module, so it needs to know where the Qt source tree is.
qt_add_executable(tst_receiver_catalog
    tst_receiver_catalog.cpp
)

target_link_libraries(tst_receiver_catalog
    PRIVATE
        ituner_core
        Qt6::Test
)

target_compile_definitions(tst_receiver_catalog
    PRIVATE ITUNER_QT_SOURCE_ROOT="${CMAKE_SOURCE_DIR}"
)

if(NOT MSVC)
    target_compile_options(tst_receiver_catalog PRIVATE -Wall -Wextra)
endif()

add_test(NAME receiver_catalog COMMAND tst_receiver_catalog)

# Tuning and waterfall expectations are captured from the same Python sources.
qt_add_executable(tst_tuning_waterfall
    tst_tuning_waterfall.cpp
)

target_link_libraries(tst_tuning_waterfall
    PRIVATE
        ituner_core
        Qt6::Test
)

target_compile_definitions(tst_tuning_waterfall
    PRIVATE ITUNER_QT_SOURCE_ROOT="${CMAKE_SOURCE_DIR}"
)

if(NOT MSVC)
    target_compile_options(tst_tuning_waterfall PRIVATE -Wall -Wextra)
endif()

add_test(NAME tuning_waterfall COMMAND tst_tuning_waterfall)

# Palette and level goldens come from the Python colour ramp and level tracker.
qt_add_executable(tst_waterfall_palette
    tst_waterfall_palette.cpp
)

target_link_libraries(tst_waterfall_palette
    PRIVATE
        ituner_core
        Qt6::Test
)

target_compile_definitions(tst_waterfall_palette
    PRIVATE ITUNER_QT_SOURCE_ROOT="${CMAKE_SOURCE_DIR}"
)

if(NOT MSVC)
    target_compile_options(tst_waterfall_palette PRIVATE -Wall -Wextra)
endif()

add_test(NAME waterfall_palette COMMAND tst_waterfall_palette)

# The state store replays the remembered-view golden captured from the Python
# load/save functions.
qt_add_executable(tst_state_store
    tst_state_store.cpp
)

target_link_libraries(tst_state_store
    PRIVATE
        ituner_core
        Qt6::Test
)

target_compile_definitions(tst_state_store
    PRIVATE ITUNER_QT_SOURCE_ROOT="${CMAKE_SOURCE_DIR}"
)

if(NOT MSVC)
    target_compile_options(tst_state_store PRIVATE -Wall -Wextra)
endif()

add_test(NAME state_store COMMAND tst_state_store)

# The swipe gesture model replays the sensitivity/tuning/repeat/zoom/inertia
# golden captured from the renderer.
qt_add_executable(tst_swipe_gesture
    tst_swipe_gesture.cpp
)

target_link_libraries(tst_swipe_gesture
    PRIVATE
        ituner_core
        Qt6::Test
)

target_compile_definitions(tst_swipe_gesture
    PRIVATE ITUNER_QT_SOURCE_ROOT="${CMAKE_SOURCE_DIR}"
)

if(NOT MSVC)
    target_compile_options(tst_swipe_gesture PRIVATE -Wall -Wextra)
endif()

add_test(NAME swipe_gesture COMMAND tst_swipe_gesture)

# The scope, the passband overlay and the waterfall control grouping replay
# goldens recorded from the real Python drawing functions.
qt_add_executable(tst_spectrum_model
    tst_spectrum_model.cpp
)

target_link_libraries(tst_spectrum_model
    PRIVATE
        ituner_core
        Qt6::Test
)

target_compile_definitions(tst_spectrum_model
    PRIVATE ITUNER_QT_SOURCE_ROOT="${CMAKE_SOURCE_DIR}"
)

target_include_directories(tst_spectrum_model PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

if(NOT MSVC)
    target_compile_options(tst_spectrum_model PRIVATE -Wall -Wextra)
endif()

add_test(NAME spectrum_model COMMAND tst_spectrum_model)

# The streamed frame test: a captured row set through the leveler, the palette
# and the row queue, checked by whole-frame checksum.
qt_add_executable(tst_waterfall_frames
    tst_waterfall_frames.cpp
)

target_link_libraries(tst_waterfall_frames
    PRIVATE
        ituner_core
        Qt6::Test
)

target_compile_definitions(tst_waterfall_frames
    PRIVATE ITUNER_QT_SOURCE_ROOT="${CMAKE_SOURCE_DIR}"
)

target_include_directories(tst_waterfall_frames PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

if(NOT MSVC)
    target_compile_options(tst_waterfall_frames PRIVATE -Wall -Wextra)
endif()

add_test(NAME waterfall_frames COMMAND tst_waterfall_frames)
