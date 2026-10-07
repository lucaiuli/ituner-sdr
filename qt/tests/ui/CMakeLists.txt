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

# The end-to-end render check: the runtime renders a captured waterfall row set
# offscreen and the result is compared, pixel for pixel, with the Python
# renderer's output for the same rows. Two streams are checked so both the
# one-row-per-line path and the row-pixel duplication path are covered.
set(ITUNER_PYTHON "${CMAKE_CURRENT_SOURCE_DIR}/../../../UI/.venv/bin/python3")
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
