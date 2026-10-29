package dev.pgproxy.app

/** Public project destinations, shared by settings labels and Android navigation. */
object ProjectLinks {
    const val REPOSITORY = "https://github.com/print-yuhuan/PG-Proxy"
    const val ISSUES = "$REPOSITORY/issues"
    const val QQ_GROUP_ONE = "807134015"
    const val QQ_GROUP_TWO = "1039656113"
    const val QQ_GROUP_GMS = "1170321366"
    val qqGroups: Set<String> = setOf(QQ_GROUP_ONE, QQ_GROUP_TWO, QQ_GROUP_GMS)
}
