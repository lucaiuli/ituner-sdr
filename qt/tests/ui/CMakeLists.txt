# The application runtime the pixel-level render checks use. It must be defined
# before any test that consumes it: an empty path makes the driver report a skip,
# which looks like a pass.
set(ITUNER_PYTHON "${CMAKE_CURRENT_SOURCE_DIR}/../../../UI/.venv/bin/python3")

# The waterfall screen's touch and display wiring, offscreen.
#
# It links the Qt Quick items, so it needs a platform plugin; the test is run with
# the offscreen plugin so it works on a machine with no display.
qt_add_executable(tst_waterfall_view
    tst_waterfall_view.cpp
)

target_link_libraries(tst_waterfall_view
    PRIVATE
        ituner_ui
        Qt6::Test
)

if(NOT MSVC)
    target_compile_options(tst_waterfall_view PRIVATE -Wall -Wextra)
endif()

add_test(NAME waterfall_view COMMAND tst_waterfall_view)
set_tests_properties(waterfall_view PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# The Home screen's wiring: the shared Back target, drawers inside the rail, and
# controls that never cover a Home instrument.
qt_add_executable(tst_home_view
    tst_home_view.cpp
)

target_link_libraries(tst_home_view
    PRIVATE
        ituner_ui
        Qt6::Test
)

if(NOT MSVC)
    target_compile_options(tst_home_view PRIVATE -Wall -Wextra)
endif()

add_test(NAME home_view COMMAND tst_home_view)
set_tests_properties(home_view PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# The end-to-end Home render: the runtime draws the Home screen offscreen and the
# frame is checked for a painted rail, tiles and instruments. Geometry is already
# pinned by `tst_navigation`; this catches a box that is right in the model and
# wrong on the screen.
add_test(NAME home_render
    COMMAND ${CMAKE_COMMAND}
        -DAPP=$<TARGET_FILE:ituner-sdr-qt>
        -DPYTHON=${ITUNER_PYTHON}
        -DVERIFY=${CMAKE_SOURCE_DIR}/tests/parity/verify_home_screen.py
        -DOUTPUT=${CMAKE_CURRENT_BINARY_DIR}/home_screen.png
        -P ${CMAKE_CURRENT_SOURCE_DIR}/run_home_render.cmake)

# The installed rail artwork: the runtime renders the Home screen with and
# without `--menu-icons` and the two frames are compared, so a directory that
# cannot be opened and a directory that loads but never draws are both caught.
# The artwork is the checked-in `UI/assets/menu-icons`, which is what an installed
# build points at.
add_test(NAME home_icons
    COMMAND ${CMAKE_COMMAND}
        -DAPP=$<TARGET_FILE:ituner-sdr-qt>
        -DPYTHON=${ITUNER_PYTHON}
        -DVERIFY=${CMAKE_SOURCE_DIR}/tests/parity/verify_home_icons.py
        -DICONS=${CMAKE_SOURCE_DIR}/../UI/assets/menu-icons
        -DWITH_ICONS=${CMAKE_CURRENT_BINARY_DIR}/home_icons.png
        -DWITHOUT_ICONS=${CMAKE_CURRENT_BINARY_DIR}/home_no_icons.png
        -P ${CMAKE_CURRENT_SOURCE_DIR}/run_home_icons.cmake)

# The drawer render checks: the runtime opens one drawer offscreen and the frame
# is checked for that drawer's own body. Geometry is already pinned by
# `drawer_bodies`; this catches a body that is right in the model and missing on
# the screen.
foreach(ITUNER_DRAWER audio display)
    add_test(NAME ${ITUNER_DRAWER}_render
        COMMAND ${CMAKE_COMMAND}
            -DAPP=$<TARGET_FILE:ituner-sdr-qt>
            -DPYTHON=${ITUNER_PYTHON}
            -DVERIFY=${CMAKE_SOURCE_DIR}/tests/parity/verify_drawer_render.py
            -DSURFACE=${ITUNER_DRAWER}
            -DOUTPUT=${CMAKE_CURRENT_BINARY_DIR}/${ITUNER_DRAWER}_drawer.png
            -P ${CMAKE_CURRENT_SOURCE_DIR}/run_drawer_render.cmake)
endforeach()

# The end-to-end render check: the runtime renders a captured waterfall row set
# offscreen and the result is compared, pixel for pixel, with the Python
# renderer's output for the same rows. Two streams are checked so both the
# one-row-per-line path and the row-pixel duplication path are covered.
set(ITUNER_WATERFALL_GOLDEN
    "${CMAKE_SOURCE_DIR}/tests/golden/waterfall_frames_expected.json")
set(ITUNER_RENDER_VERIFY "${CMAKE_SOURCE_DIR}/tests/parity/verify_waterfall_render.py")

add_test(NAME waterfall_render_row1
    COMMAND ${CMAKE_COMMAND}
        -DAPP=$<TARGET_FILE:ituner-sdr-qt>
        -DPYTHON=${ITUNER_PYTHON}
        -DGOLDEN=${ITUNER_WATERFALL_GOLDEN}
        -DVERIFY=${ITUNER_RENDER_VERIFY}
        -DSTREAM=0
        -DOUTPUT=${CMAKE_CURRENT_BINARY_DIR}/waterfall_row1.png
        -P ${CMAKE_CURRENT_SOURCE_DIR}/run_waterfall_render.cmake)

add_test(NAME waterfall_render_row2
    COMMAND ${CMAKE_COMMAND}
        -DAPP=$<TARGET_FILE:ituner-sdr-qt>
        -DPYTHON=${ITUNER_PYTHON}
        -DGOLDEN=${ITUNER_WATERFALL_GOLDEN}
        -DVERIFY=${ITUNER_RENDER_VERIFY}
        -DSTREAM=1
        -DOUTPUT=${CMAKE_CURRENT_BINARY_DIR}/waterfall_row2.png
        -P ${CMAKE_CURRENT_SOURCE_DIR}/run_waterfall_render.cmake)
