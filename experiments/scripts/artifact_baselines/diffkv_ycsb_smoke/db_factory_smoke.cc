#include "db/db_factory.h"

#include "titandb_db_smoke.h"

using ycsbc::DB;
using ycsbc::DBFactory;

DB* DBFactory::CreateDB(utils::Properties& props) {
  if (props["dbname"] == "diffkv" || props["dbname"] == "titandb") {
    return new ycsbc::TitanDBSmoke(props["dbfilename"].c_str(),
                                  props["configpath"]);
  }
  return nullptr;
}
