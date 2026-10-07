# The KiwiSDR transport parity test replays the golden captured from the Python
# protocol helpers, so it needs to know where the Qt source tree is.
qt_add_executable(tst_kiwi_transport
    tst_kiwi_transport.cpp
)

target_link_libraries(tst_kiwi_transport
    PRIVATE
        ituner_transport
        Qt6::Test
)

target_compile_definitions(tst_kiwi_transport
    PRIVATE ITUNER_QT_SOURCE_ROOT="${CMAKE_SOURCE_DIR}"
)

if(NOT MSVC)
    target_compile_options(tst_kiwi_transport PRIVATE -Wall -Wextra)
endif()

add_test(NAME kiwi_transport COMMAND tst_kiwi_transport)
