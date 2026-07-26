import pymysql


# PyMySQL 1.2 exposes the MySQLdb/mysqlclient compatibility API expected by
# Django without compiling native client headers in Nixpacks.
pymysql.install_as_MySQLdb()
